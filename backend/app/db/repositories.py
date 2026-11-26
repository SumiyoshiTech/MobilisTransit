"""Parameterized SQLAlchemy 2.0 repositories for routes, journeys, experiments, ML & telemetry."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.app.db.base import normalize_utc, utc_now
from backend.app.db.models import (
    ExperimentJobModel,
    ExperimentRunModel,
    JourneyModel,
    ObservedStopArrivalModel,
    RouteModel,
    StopModel,
    TelemetryEventModel,
    VehicleSnapshotModel,
)
from backend.app.schemas.contracts import (
    DemandProfile,
    ExperimentComparisonDto,
    ExperimentCreateRequest,
    ExperimentDetailDto,
    ExperimentInvariantDto,
    ExperimentJobStatus,
    JourneyStatus,
    JourneySummaryDto,
    ObservedStopComparisonRowDto,
    PlaybackState,
    RouteDto,
    RouteStopDto,
    SnapshotSource,
    TelemetryEventIn,
    TelemetryGroupSummaryDto,
    TelemetrySummaryDto,
    TransportMode,
)
from backend.app.simulation.manifest_loader import NetworkManifest
from backend.app.simulation.traci_runner import SimulationRunResult

ACTIVE_EXPERIMENT_STATUSES: tuple[str, ...] = ("QUEUED", "RUNNING")
FAILURE_TELEMETRY_STATUSES: frozenset[str] = frozenset({"ERROR", "TIMEOUT"})
DISCARDED_TELEMETRY_STATUSES: frozenset[str] = frozenset({"DISCARDED_STALE", "DISCARDED_SCOPE"})

ML_JOURNEYS_COLUMNS: list[str] = [
    "journey_id",
    "route_id",
    "scenario_id",
    "execution_id",
    "experiment_family_id",
    "source",
    "network_version",
    "demand_profile",
    "traffic_multiplier",
    "stop_hold_s",
    "intervention_stop_id",
    "departure_time_s",
    "random_seed",
    "status",
    "playback_state",
    "sim_duration_s",
    "completed_cleanly",
    "held_out_candidate",
    "is_disrupted",
    "created_at",
]

ML_SNAPSHOTS_COLUMNS: list[str] = [
    "journey_id",
    "sequence_number",
    "simulation_clock_s",
    "vehicle_id",
    "status",
    "edge_id",
    "lane_position_m",
    "distance_travelled_m",
    "speed_mps",
    "current_segment_index",
    "last_visited_stop_id",
    "next_stop_id",
    "ongoing_stop_elapsed_s",
    "last_completed_stop_dwell_s",
    "worker_generated_at_ms",
]

ML_ARRIVALS_COLUMNS: list[str] = [
    "journey_id",
    "route_id",
    "stop_id",
    "sequence_index",
    "sumo_bus_stop_id",
    "observed_arrival_s",
    "dwell_duration_s",
]


class RepositoryError(Exception):
    """Base domain error raised by database repositories."""


class EntityNotFoundError(RepositoryError):
    """Raised when a requested route, journey, or experiment record does not exist."""


class DomainValidationError(RepositoryError):
    """Raised when a repository operation violates domain invariants (`BR-005`–`BR-007`)."""


def _format_experiment_id(seq_id: int) -> str:
    return f"EXP-{seq_id:03d}"


def compute_experiment_idempotency_hash(
    request: ExperimentCreateRequest,
    network_version: str,
) -> str:
    """Compute a deterministic SHA-256 fingerprint for experiment deduplication (`BR-008`)."""
    if request.idempotency_key:
        payload: Any = {
            "mode": "explicit",
            "route_id": request.route_id,
            "idempotency_key": request.idempotency_key,
        }
    else:
        payload = {
            "mode": "canonical",
            "route_id": request.route_id,
            "network_version": network_version,
            "demand_profile": request.demand_profile,
            "departure_time_s": request.departure_time_s,
            "random_seed": request.random_seed,
            "intervention_stop_id": request.intervention_stop_id,
            "stop_hold_s": request.stop_hold_s,
            "traffic_multiplier": round(request.traffic_multiplier, 3),
        }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def seed_route_from_manifest(session: Session, manifest: NetworkManifest) -> RouteModel:
    """Idempotently upsert a route and all ordered stops from a validated `NetworkManifest`."""
    now = utc_now()
    route = session.get(RouteModel, manifest.route_id)
    if route is None:
        route = RouteModel(
            route_id=manifest.route_id,
            route_code=manifest.route_code,
            route_name=manifest.route_name,
            network_version=manifest.network_version,
            calibration_notes=manifest.calibration_notes,
            created_at=now,
            updated_at=now,
        )
        session.add(route)
        session.flush()
    else:
        route.route_code = manifest.route_code
        route.route_name = manifest.route_name
        route.network_version = manifest.network_version
        route.calibration_notes = manifest.calibration_notes
        route.updated_at = now

    existing_stops = {
        stop.stop_id: stop
        for stop in session.scalars(
            select(StopModel).where(StopModel.route_id == manifest.route_id)
        ).all()
    }
    manifest_stop_ids = {stop.stop_id for stop in manifest.stops}

    # Remove obsolete stops that are not referenced by observed arrivals.
    obsolete_ids = sorted(set(existing_stops.keys()) - manifest_stop_ids)
    if obsolete_ids:
        referenced_ids = set(
            session.scalars(
                select(ObservedStopArrivalModel.stop_id)
                .where(
                    ObservedStopArrivalModel.route_id == manifest.route_id,
                    ObservedStopArrivalModel.stop_id.in_(obsolete_ids),
                )
                .distinct()
            ).all()
        )
        for stop_id in obsolete_ids:
            if stop_id not in referenced_ids:
                session.delete(existing_stops.pop(stop_id))
        session.flush()

    # Temporarily shift sequence indices of retained stops whose order changed (or would collide
    # with newly added stops) to avoid transient collisions on `uq_stops_route_sequence`.
    target_indices = {s.stop_id: s.sequence_index for s in manifest.stops}
    new_stop_indices = {
        s.sequence_index for s in manifest.stops if s.stop_id not in existing_stops
    }
    needs_shift = any(
        stop.sequence_index != target_indices.get(stop_id, stop.sequence_index)
        or stop.sequence_index in new_stop_indices
        for stop_id, stop in existing_stops.items()
    )
    if needs_shift:
        for offset, stop in enumerate(existing_stops.values(), start=100_000):
            stop.sequence_index = offset
        session.flush()

    for stop_manifest in manifest.stops:
        existing = existing_stops.get(stop_manifest.stop_id)
        if existing is None:
            session.add(
                StopModel(
                    route_id=manifest.route_id,
                    stop_id=stop_manifest.stop_id,
                    sumo_bus_stop_id=stop_manifest.sumo_bus_stop_id,
                    name=stop_manifest.name,
                    sequence_index=stop_manifest.sequence_index,
                    distance_from_start_m=stop_manifest.distance_from_start_m,
                    x_norm=stop_manifest.x_norm,
                    y_norm=stop_manifest.y_norm,
                    reference_offset_s=stop_manifest.reference_offset_s,
                    ar_marker_id=stop_manifest.ar_marker_id,
                    ar_marker_width_m=stop_manifest.ar_marker_width_m,
                )
            )
        else:
            existing.sumo_bus_stop_id = stop_manifest.sumo_bus_stop_id
            existing.name = stop_manifest.name
            existing.sequence_index = stop_manifest.sequence_index
            existing.distance_from_start_m = stop_manifest.distance_from_start_m
            existing.x_norm = stop_manifest.x_norm
            existing.y_norm = stop_manifest.y_norm
            existing.reference_offset_s = stop_manifest.reference_offset_s
            existing.ar_marker_id = stop_manifest.ar_marker_id
            existing.ar_marker_width_m = stop_manifest.ar_marker_width_m

    session.flush()
    return route


def list_routes(session: Session) -> list[RouteDto]:
    """Return all configured routes with their stop counts."""
    rows = session.execute(
        select(RouteModel, func.count(StopModel.stop_id))
        .outerjoin(StopModel, StopModel.route_id == RouteModel.route_id)
        .group_by(RouteModel.route_id)
        .order_by(RouteModel.route_id.asc())
    ).all()
    return [
        RouteDto(
            route_id=route.route_id,
            route_code=route.route_code,
            route_name=route.route_name,
            network_version=route.network_version,
            calibration_notes=route.calibration_notes,
            stop_count=int(stop_count),
        )
        for route, stop_count in rows
    ]


def get_route_stops(session: Session, route_id: str) -> list[RouteStopDto]:
    """Return ordered stops for `route_id`, raising `EntityNotFoundError` if route is missing."""
    route = session.get(RouteModel, route_id)
    if route is None:
        raise EntityNotFoundError(f"Route {route_id!r} does not exist.")
    stops = session.scalars(
        select(StopModel)
        .where(StopModel.route_id == route_id)
        .order_by(StopModel.sequence_index.asc())
    ).all()
    return [RouteStopDto.model_validate(stop) for stop in stops]


def stop_exists_on_route(session: Session, route_id: str, stop_id: str) -> bool:
    """Return True if `(route_id, stop_id)` exists in the `stops` table."""
    return session.get(StopModel, (route_id, stop_id)) is not None


def save_completed_journey(
    session: Session,
    run_result: SimulationRunResult,
    route_id: str,
    source: SnapshotSource = "SIMULATION",
    experiment_family_id: str | None = None,
    *,
    status: JourneyStatus | None = None,
    playback_state: PlaybackState = "STOPPED",
    held_out_candidate: bool = False,
) -> JourneyModel:
    """Atomically persist a journey, all vehicle snapshots, and all observed stop arrivals."""
    route = session.get(RouteModel, route_id)
    if route is None:
        raise EntityNotFoundError(f"Route {route_id!r} does not exist.")

    cfg = run_result.config
    effective_status: JourneyStatus = (
        status if status is not None else ("COMPLETED" if run_result.completed_cleanly else "FAILED")
    )

    with session.begin_nested():
        journey = JourneyModel(
            journey_id=cfg.journey_id,
            route_id=route_id,
            scenario_id=cfg.scenario_id,
            execution_id=cfg.execution_id,
            experiment_family_id=experiment_family_id,
            source=source,
            network_version=run_result.network_version,
            demand_profile=cfg.demand_profile,
            traffic_multiplier=cfg.traffic_multiplier,
            stop_hold_s=cfg.stop_hold_s,
            intervention_stop_id=cfg.intervention_stop_id,
            departure_time_s=cfg.departure_time_s,
            random_seed=cfg.random_seed,
            status=effective_status,
            playback_state=playback_state,
            sim_duration_s=run_result.sim_duration_s,
            completed_cleanly=run_result.completed_cleanly,
            held_out_candidate=held_out_candidate,
            created_at=utc_now(),
        )
        session.add(journey)
        session.flush()

        snapshot_rows = [
            VehicleSnapshotModel(
                journey_id=cfg.journey_id,
                sequence_number=snap.sequence_number,
                simulation_clock_s=snap.simulation_clock_s,
                vehicle_id=snap.vehicle_id,
                status=snap.status,
                edge_id=snap.edge_id,
                lane_position_m=snap.lane_position_m,
                distance_travelled_m=snap.distance_travelled_m,
                speed_mps=snap.speed_mps,
                current_segment_index=snap.current_segment_index,
                last_visited_stop_id=snap.last_visited_stop_id,
                next_stop_id=snap.next_stop_id,
                ongoing_stop_elapsed_s=snap.ongoing_stop_elapsed_s,
                last_completed_stop_dwell_s=snap.last_completed_stop_dwell_s,
                worker_generated_at_ms=snap.worker_generated_at_ms,
            )
            for snap in run_result.raw_snapshots
        ]
        if snapshot_rows:
            session.add_all(snapshot_rows)

        arrival_rows = [
            ObservedStopArrivalModel(
                journey_id=cfg.journey_id,
                route_id=route_id,
                stop_id=arr.stop_id,
                sequence_index=arr.sequence_index,
                sumo_bus_stop_id=arr.sumo_bus_stop_id,
                observed_arrival_s=arr.observed_arrival_s,
                dwell_duration_s=arr.dwell_duration_s,
            )
            for arr in run_result.observed_arrivals
        ]
        if arrival_rows:
            session.add_all(arrival_rows)

        session.flush()

    return journey


def list_journeys(
    session: Session,
    route_id: str | None = None,
    source: str | None = None,
    status: str | None = None,
) -> list[JourneySummaryDto]:
    """List persisted journeys ordered newest-first, with optional filters."""
    stmt = select(JourneyModel).order_by(
        JourneyModel.created_at.desc(),
        JourneyModel.journey_id.desc(),
    )
    if route_id is not None:
        stmt = stmt.where(JourneyModel.route_id == route_id)
    if source is not None:
        stmt = stmt.where(JourneyModel.source == source)
    if status is not None:
        stmt = stmt.where(JourneyModel.status == status)

    rows = session.scalars(stmt).all()
    return [JourneySummaryDto.model_validate(row) for row in rows]


def get_journey(session: Session, journey_id: str) -> JourneyModel | None:
    """Fetch a single `JourneyModel` by primary key."""
    return session.get(JourneyModel, journey_id)


def get_journey_snapshots(session: Session, journey_id: str) -> list[VehicleSnapshotModel]:
    """Return ordered snapshots for `journey_id`."""
    return list(
        session.scalars(
            select(VehicleSnapshotModel)
            .where(VehicleSnapshotModel.journey_id == journey_id)
            .order_by(VehicleSnapshotModel.sequence_number.asc())
        ).all()
    )


def _allocate_experiment_run(
    session: Session,
    *,
    job_id: str,
    role: str,
    route_id: str,
    network_version: str,
    demand_profile: str,
    departure_time_s: int,
    random_seed: int,
    intervention_stop_id: str | None,
    stop_hold_s: int,
    traffic_multiplier: float,
) -> ExperimentRunModel:
    """Insert an `ExperimentRunModel` and derive its unique `EXP-xxx` ID from autoincrement PK."""
    run = ExperimentRunModel(
        experiment_id=f"PENDING-{uuid4().hex[:20]}",
        job_id=job_id,
        journey_id=None,
        role=role,
        route_id=route_id,
        network_version=network_version,
        demand_profile=demand_profile,
        departure_time_s=departure_time_s,
        random_seed=random_seed,
        intervention_stop_id=intervention_stop_id,
        stop_hold_s=stop_hold_s,
        traffic_multiplier=traffic_multiplier,
        created_at=utc_now(),
    )
    session.add(run)
    session.flush()
    run.experiment_id = _format_experiment_id(run.seq_id)
    session.flush()
    return run


def _find_active_experiment_job(
    session: Session,
    idempotency_hash: str,
) -> ExperimentJobModel | None:
    return session.scalars(
        select(ExperimentJobModel)
        .where(
            ExperimentJobModel.idempotency_hash == idempotency_hash,
            ExperimentJobModel.status.in_(ACTIVE_EXPERIMENT_STATUSES),
        )
        .order_by(ExperimentJobModel.created_at.desc())
        .limit(1)
    ).first()


def create_experiment_job(
    session: Session,
    request: ExperimentCreateRequest,
    *,
    network_version: str | None = None,
) -> tuple[ExperimentJobModel, bool]:
    """Create or deduplicate a paired experiment job and allocate sequential `EXP-xxx` IDs.

    Returns ``(job, deduplicated)``. Enforces route/stop existence (`BR-007`),
    paired run invariance (`BR-005`), atomic active-job deduplication (`BR-008`) via
    `uq_experiment_jobs_active_idempotency`, and race-free sequential ID derivation
    from `experiment_runs.seq_id`.
    """
    route = session.get(RouteModel, request.route_id)
    if route is None:
        raise EntityNotFoundError(f"Route {request.route_id!r} does not exist.")

    if request.intervention_stop_id is not None and not stop_exists_on_route(
        session, request.route_id, request.intervention_stop_id
    ):
        raise DomainValidationError(
            f"Unknown intervention_stop_id {request.intervention_stop_id!r} "
            f"for route {request.route_id!r} (BR-007)."
        )

    resolved_network_version = network_version or route.network_version
    idempotency_hash = compute_experiment_idempotency_hash(request, resolved_network_version)

    existing_active = _find_active_experiment_job(session, idempotency_hash)
    if existing_active is not None:
        return existing_active, True

    now = utc_now()
    job_id = f"JOB-{uuid4().hex[:12].upper()}"

    try:
        with session.begin_nested():
            job = ExperimentJobModel(
                job_id=job_id,
                name=request.name,
                route_id=request.route_id,
                demand_profile=request.demand_profile,
                departure_time_s=request.departure_time_s,
                random_seed=request.random_seed,
                intervention_stop_id=request.intervention_stop_id,
                stop_hold_s=request.stop_hold_s,
                traffic_multiplier=request.traffic_multiplier,
                idempotency_hash=idempotency_hash,
                status="QUEUED",
                created_at=now,
            )
            session.add(job)
            session.flush()

            baseline_run = _allocate_experiment_run(
                session,
                job_id=job.job_id,
                role="BASELINE",
                route_id=request.route_id,
                network_version=resolved_network_version,
                demand_profile=request.demand_profile,
                departure_time_s=request.departure_time_s,
                random_seed=request.random_seed,
                intervention_stop_id=None,
                stop_hold_s=0,
                traffic_multiplier=1.0,
            )
            perturbed_run = _allocate_experiment_run(
                session,
                job_id=job.job_id,
                role="PERTURBED",
                route_id=request.route_id,
                network_version=resolved_network_version,
                demand_profile=request.demand_profile,
                departure_time_s=request.departure_time_s,
                random_seed=request.random_seed,
                intervention_stop_id=request.intervention_stop_id,
                stop_hold_s=request.stop_hold_s,
                traffic_multiplier=request.traffic_multiplier,
            )

            job.baseline_experiment_id = baseline_run.experiment_id
            job.perturbed_experiment_id = perturbed_run.experiment_id
            session.flush()
    except IntegrityError:
        existing_active = _find_active_experiment_job(session, idempotency_hash)
        if existing_active is not None:
            return existing_active, True
        raise

    return job, False


def update_experiment_job_status(
    session: Session,
    job_id: str,
    status: ExperimentJobStatus,
    *,
    error_message: str | None = None,
    baseline_journey_id: str | None = None,
    perturbed_journey_id: str | None = None,
) -> ExperimentJobModel:
    """Update experiment job lifecycle timestamps, error message, and linked journey IDs."""
    job = session.scalars(
        select(ExperimentJobModel)
        .options(selectinload(ExperimentJobModel.runs))
        .where(ExperimentJobModel.job_id == job_id)
    ).first()
    if job is None:
        raise EntityNotFoundError(f"Experiment job {job_id!r} does not exist.")

    now = utc_now()
    job.status = status
    if status == "RUNNING" and job.started_at is None:
        job.started_at = now
    if status in ("COMPLETED", "FAILED"):
        if job.started_at is None:
            job.started_at = now
        job.completed_at = now
    if error_message is not None:
        job.error_message = error_message

    runs_by_role = {run.role: run for run in job.runs}
    if baseline_journey_id is not None and "BASELINE" in runs_by_role:
        runs_by_role["BASELINE"].journey_id = baseline_journey_id
    if perturbed_journey_id is not None and "PERTURBED" in runs_by_role:
        runs_by_role["PERTURBED"].journey_id = perturbed_journey_id

    session.flush()
    return job


def compare_experiment_runs(
    session: Session,
    baseline_exp_id: str,
    perturbed_exp_id: str,
) -> ExperimentComparisonDto:
    """Compute stop-by-stop observed arrival differences strictly from `observed_stop_arrivals`."""
    baseline_run = session.scalars(
        select(ExperimentRunModel).where(ExperimentRunModel.experiment_id == baseline_exp_id)
    ).first()
    if baseline_run is None:
        raise EntityNotFoundError(f"Baseline experiment {baseline_exp_id!r} does not exist.")

    perturbed_run = session.scalars(
        select(ExperimentRunModel).where(ExperimentRunModel.experiment_id == perturbed_exp_id)
    ).first()
    if perturbed_run is None:
        raise EntityNotFoundError(f"Perturbed experiment {perturbed_exp_id!r} does not exist.")

    if baseline_run.journey_id is None:
        raise DomainValidationError(
            f"Baseline experiment {baseline_exp_id!r} has no completed journey recorded yet."
        )
    if perturbed_run.journey_id is None:
        raise DomainValidationError(
            f"Perturbed experiment {perturbed_exp_id!r} has no completed journey recorded yet."
        )

    invariants_match = (
        baseline_run.route_id == perturbed_run.route_id
        and baseline_run.network_version == perturbed_run.network_version
        and baseline_run.demand_profile == perturbed_run.demand_profile
        and baseline_run.departure_time_s == perturbed_run.departure_time_s
        and baseline_run.random_seed == perturbed_run.random_seed
    )

    stops = session.scalars(
        select(StopModel)
        .where(StopModel.route_id == baseline_run.route_id)
        .order_by(StopModel.sequence_index.asc())
    ).all()

    baseline_arrivals = {
        row.stop_id: row
        for row in session.scalars(
            select(ObservedStopArrivalModel).where(
                ObservedStopArrivalModel.journey_id == baseline_run.journey_id
            )
        ).all()
    }
    perturbed_arrivals = {
        row.stop_id: row
        for row in session.scalars(
            select(ObservedStopArrivalModel).where(
                ObservedStopArrivalModel.journey_id == perturbed_run.journey_id
            )
        ).all()
    }

    rows: list[ObservedStopComparisonRowDto] = []
    diffs: list[int] = []
    for stop in stops:
        base_row = baseline_arrivals.get(stop.stop_id)
        pert_row = perturbed_arrivals.get(stop.stop_id)
        base_arrival = base_row.observed_arrival_s if base_row is not None else None
        pert_arrival = pert_row.observed_arrival_s if pert_row is not None else None
        diff_s = (
            pert_arrival - base_arrival
            if (base_arrival is not None and pert_arrival is not None)
            else None
        )
        if diff_s is not None:
            diffs.append(diff_s)

        rows.append(
            ObservedStopComparisonRowDto(
                stop_id=stop.stop_id,
                stop_name=stop.name,
                sequence_index=stop.sequence_index,
                baseline_observed_arrival_s=base_arrival,
                perturbed_observed_arrival_s=pert_arrival,
                observed_difference_s=diff_s,
                baseline_dwell_s=base_row.dwell_duration_s if base_row is not None else None,
                perturbed_dwell_s=pert_row.dwell_duration_s if pert_row is not None else None,
            )
        )

    invariants = ExperimentInvariantDto(
        route_id=baseline_run.route_id,
        network_version=baseline_run.network_version,
        demand_profile=cast(DemandProfile, baseline_run.demand_profile),
        departure_time_s=baseline_run.departure_time_s,
        random_seed=baseline_run.random_seed,
    )

    return ExperimentComparisonDto(
        baseline_experiment_id=baseline_run.experiment_id,
        perturbed_experiment_id=perturbed_run.experiment_id,
        invariants=invariants,
        invariants_match=invariants_match,
        intervention_stop_id=perturbed_run.intervention_stop_id,
        stop_hold_s=perturbed_run.stop_hold_s,
        traffic_multiplier=perturbed_run.traffic_multiplier,
        rows=rows,
        max_observed_difference_s=max(diffs) if diffs else None,
        mean_observed_difference_s=(round(sum(diffs) / len(diffs), 2) if diffs else None),
    )


def _job_to_detail_dto(
    session: Session,
    job: ExperimentJobModel,
    *,
    include_comparison: bool,
) -> ExperimentDetailDto:
    runs_by_role = {run.role: run for run in job.runs}
    baseline_run = runs_by_role.get("BASELINE")
    perturbed_run = runs_by_role.get("PERTURBED")

    network_version = (
        baseline_run.network_version
        if baseline_run is not None
        else (perturbed_run.network_version if perturbed_run is not None else None)
    )
    invariants = (
        ExperimentInvariantDto(
            route_id=job.route_id,
            network_version=network_version,
            demand_profile=cast(DemandProfile, job.demand_profile),
            departure_time_s=job.departure_time_s,
            random_seed=job.random_seed,
        )
        if network_version is not None
        else None
    )

    comparison: ExperimentComparisonDto | None = None
    if (
        include_comparison
        and job.status == "COMPLETED"
        and baseline_run is not None
        and perturbed_run is not None
        and baseline_run.journey_id is not None
        and perturbed_run.journey_id is not None
    ):
        comparison = compare_experiment_runs(
            session,
            baseline_run.experiment_id,
            perturbed_run.experiment_id,
        )

    return ExperimentDetailDto(
        job_id=job.job_id,
        name=job.name,
        status=cast(ExperimentJobStatus, job.status),
        route_id=job.route_id,
        network_version=network_version,
        demand_profile=cast(DemandProfile, job.demand_profile),
        departure_time_s=job.departure_time_s,
        random_seed=job.random_seed,
        intervention_stop_id=job.intervention_stop_id,
        stop_hold_s=job.stop_hold_s,
        traffic_multiplier=job.traffic_multiplier,
        baseline_experiment_id=job.baseline_experiment_id,
        perturbed_experiment_id=job.perturbed_experiment_id,
        baseline_journey_id=baseline_run.journey_id if baseline_run is not None else None,
        perturbed_journey_id=perturbed_run.journey_id if perturbed_run is not None else None,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        invariants=invariants,
        comparison=comparison,
    )


def get_experiment_detail(session: Session, identifier: str) -> ExperimentDetailDto:
    """Resolve an experiment by `job_id` or `EXP-xxx` ID and return its full detail DTO."""
    job = session.scalars(
        select(ExperimentJobModel)
        .options(selectinload(ExperimentJobModel.runs))
        .where(ExperimentJobModel.job_id == identifier)
    ).first()

    if job is None:
        run = session.scalars(
            select(ExperimentRunModel).where(ExperimentRunModel.experiment_id == identifier)
        ).first()
        if run is not None:
            job = session.scalars(
                select(ExperimentJobModel)
                .options(selectinload(ExperimentJobModel.runs))
                .where(ExperimentJobModel.job_id == run.job_id)
            ).first()

    if job is None:
        raise EntityNotFoundError(f"Experiment or job {identifier!r} does not exist.")

    return _job_to_detail_dto(session, job, include_comparison=True)


def list_experiments(
    session: Session,
    route_id: str | None = None,
    demand_profile: str | None = None,
    min_hold_s: int | None = None,
    max_hold_s: int | None = None,
    min_traffic_multiplier: float | None = None,
    max_traffic_multiplier: float | None = None,
    status: str | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
) -> list[ExperimentDetailDto]:
    """Filter and list stored paired experiment jobs (`REQ-003`, `REQ-014`, `REQ-015`)."""
    stmt = (
        select(ExperimentJobModel)
        .options(selectinload(ExperimentJobModel.runs))
        .order_by(ExperimentJobModel.created_at.desc(), ExperimentJobModel.job_id.desc())
    )
    if route_id is not None:
        stmt = stmt.where(ExperimentJobModel.route_id == route_id)
    if demand_profile is not None:
        stmt = stmt.where(ExperimentJobModel.demand_profile == demand_profile)
    if min_hold_s is not None:
        stmt = stmt.where(ExperimentJobModel.stop_hold_s >= min_hold_s)
    if max_hold_s is not None:
        stmt = stmt.where(ExperimentJobModel.stop_hold_s <= max_hold_s)
    if min_traffic_multiplier is not None:
        stmt = stmt.where(ExperimentJobModel.traffic_multiplier >= min_traffic_multiplier)
    if max_traffic_multiplier is not None:
        stmt = stmt.where(ExperimentJobModel.traffic_multiplier <= max_traffic_multiplier)
    if status is not None:
        stmt = stmt.where(ExperimentJobModel.status == status)
    if from_time is not None:
        stmt = stmt.where(ExperimentJobModel.created_at >= normalize_utc(from_time))
    if to_time is not None:
        stmt = stmt.where(ExperimentJobModel.created_at <= normalize_utc(to_time))

    jobs = session.scalars(stmt).all()
    return [_job_to_detail_dto(session, job, include_comparison=False) for job in jobs]


def export_journeys_for_ml(
    session: Session,
    route_id: str | None = None,
    *,
    completed_only: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Export `(journeys_df, snapshots_df, arrivals_df)` for offline AAP ML pipelines (`T005`)."""
    stmt = select(JourneyModel).order_by(
        JourneyModel.created_at.asc(),
        JourneyModel.journey_id.asc(),
    )
    if route_id is not None:
        stmt = stmt.where(JourneyModel.route_id == route_id)
    if completed_only:
        stmt = stmt.where(
            JourneyModel.status == "COMPLETED",
            JourneyModel.completed_cleanly.is_(True),
        )

    journeys = session.scalars(stmt).all()
    if not journeys:
        return (
            pd.DataFrame(columns=ML_JOURNEYS_COLUMNS),
            pd.DataFrame(columns=ML_SNAPSHOTS_COLUMNS),
            pd.DataFrame(columns=ML_ARRIVALS_COLUMNS),
        )

    journey_ids = [j.journey_id for j in journeys]

    journeys_records = [
        {
            "journey_id": j.journey_id,
            "route_id": j.route_id,
            "scenario_id": j.scenario_id,
            "execution_id": j.execution_id,
            "experiment_family_id": j.experiment_family_id,
            "source": j.source,
            "network_version": j.network_version,
            "demand_profile": j.demand_profile,
            "traffic_multiplier": j.traffic_multiplier,
            "stop_hold_s": j.stop_hold_s,
            "intervention_stop_id": j.intervention_stop_id,
            "departure_time_s": j.departure_time_s,
            "random_seed": j.random_seed,
            "status": j.status,
            "playback_state": j.playback_state,
            "sim_duration_s": j.sim_duration_s,
            "completed_cleanly": j.completed_cleanly,
            "held_out_candidate": j.held_out_candidate,
            "is_disrupted": bool(
                j.stop_hold_s > 0 or not math.isclose(j.traffic_multiplier, 1.0, abs_tol=1e-9)
            ),
            "created_at": j.created_at.isoformat(),
        }
        for j in journeys
    ]

    snapshots = session.scalars(
        select(VehicleSnapshotModel)
        .where(VehicleSnapshotModel.journey_id.in_(journey_ids))
        .order_by(
            VehicleSnapshotModel.journey_id.asc(),
            VehicleSnapshotModel.sequence_number.asc(),
        )
    ).all()
    snapshots_records = [
        {
            "journey_id": s.journey_id,
            "sequence_number": s.sequence_number,
            "simulation_clock_s": s.simulation_clock_s,
            "vehicle_id": s.vehicle_id,
            "status": s.status,
            "edge_id": s.edge_id,
            "lane_position_m": s.lane_position_m,
            "distance_travelled_m": s.distance_travelled_m,
            "speed_mps": s.speed_mps,
            "current_segment_index": s.current_segment_index,
            "last_visited_stop_id": s.last_visited_stop_id,
            "next_stop_id": s.next_stop_id,
            "ongoing_stop_elapsed_s": s.ongoing_stop_elapsed_s,
            "last_completed_stop_dwell_s": s.last_completed_stop_dwell_s,
            "worker_generated_at_ms": s.worker_generated_at_ms,
        }
        for s in snapshots
    ]

    arrivals = session.scalars(
        select(ObservedStopArrivalModel)
        .where(ObservedStopArrivalModel.journey_id.in_(journey_ids))
        .order_by(
            ObservedStopArrivalModel.journey_id.asc(),
            ObservedStopArrivalModel.sequence_index.asc(),
        )
    ).all()
    arrivals_records = [
        {
            "journey_id": a.journey_id,
            "route_id": a.route_id,
            "stop_id": a.stop_id,
            "sequence_index": a.sequence_index,
            "sumo_bus_stop_id": a.sumo_bus_stop_id,
            "observed_arrival_s": a.observed_arrival_s,
            "dwell_duration_s": a.dwell_duration_s,
        }
        for a in arrivals
    ]

    journeys_df = pd.DataFrame(journeys_records, columns=ML_JOURNEYS_COLUMNS)
    snapshots_df = pd.DataFrame(snapshots_records, columns=ML_SNAPSHOTS_COLUMNS)
    arrivals_df = pd.DataFrame(arrivals_records, columns=ML_ARRIVALS_COLUMNS)
    return journeys_df, snapshots_df, arrivals_df


def save_telemetry_events(session: Session, events: Sequence[TelemetryEventIn]) -> int:
    """Persist a batch of client/server wall-clock telemetry events and return inserted count."""
    if not events:
        return 0

    now = utc_now()
    models = [
        TelemetryEventModel(
            journey_id=ev.journey_id,
            scenario_id=ev.scenario_id,
            execution_id=ev.execution_id,
            sequence_number=ev.sequence_number,
            transport_mode=ev.transport_mode,
            poll_interval_s=ev.poll_interval_s,
            worker_generated_at_ms=ev.worker_generated_at_ms,
            prediction_completed_at_ms=ev.prediction_completed_at_ms,
            server_published_at_ms=ev.server_published_at_ms,
            client_request_start_ms=ev.client_request_start_ms,
            client_received_at_ms=ev.client_received_at_ms,
            client_rendered_at_ms=ev.client_rendered_at_ms,
            clock_offset_ms=ev.clock_offset_ms,
            status=ev.status,
            error_code=ev.error_code,
            recorded_at=now,
        )
        for ev in events
    ]
    session.add_all(models)
    session.flush()
    return len(models)


def _median_and_p95(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    arr = np.asarray(values, dtype=np.float64)
    return (
        round(float(np.percentile(arr, 50)), 2),
        round(float(np.percentile(arr, 95)), 2),
    )


def summarize_telemetry(
    session: Session,
    transport_mode: str | None = None,
) -> TelemetrySummaryDto:
    """Aggregate median, P95, and failure rates per `(transport_mode, poll_interval_s)`."""
    stmt = select(TelemetryEventModel)
    if transport_mode is not None:
        stmt = stmt.where(TelemetryEventModel.transport_mode == transport_mode)

    events = session.scalars(stmt).all()
    if not events:
        return TelemetrySummaryDto(total_events=0, groups=[])

    grouped: dict[tuple[str, int | None], list[TelemetryEventModel]] = defaultdict(list)
    for ev in events:
        grouped[(ev.transport_mode, ev.poll_interval_s)].append(ev)

    groups: list[TelemetryGroupSummaryDto] = []
    for (mode, poll_interval), group_events in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], -1 if item[0][1] is None else item[0][1]),
    ):
        total = len(group_events)
        success_count = sum(1 for ev in group_events if ev.status == "OK")
        failure_count = sum(1 for ev in group_events if ev.status in FAILURE_TELEMETRY_STATUSES)
        discarded_count = sum(
            1 for ev in group_events if ev.status in DISCARDED_TELEMETRY_STATUSES
        )

        server_to_display: list[float] = []
        request_to_display: list[float] = []
        freshness: list[float] = []

        for ev in group_events:
            if ev.status != "OK" or ev.client_rendered_at_ms is None:
                continue
            aligned_rendered_ms = ev.client_rendered_at_ms + ev.clock_offset_ms
            if ev.server_published_at_ms is not None:
                server_to_display.append(float(aligned_rendered_ms - ev.server_published_at_ms))
            if ev.client_request_start_ms is not None:
                request_to_display.append(
                    float(ev.client_rendered_at_ms - ev.client_request_start_ms)
                )
            if ev.worker_generated_at_ms is not None:
                freshness.append(float(aligned_rendered_ms - ev.worker_generated_at_ms))

        s2d_med, s2d_p95 = _median_and_p95(server_to_display)
        r2d_med, r2d_p95 = _median_and_p95(request_to_display)
        fresh_med, fresh_p95 = _median_and_p95(freshness)

        groups.append(
            TelemetryGroupSummaryDto(
                transport_mode=cast(TransportMode, mode),
                poll_interval_s=poll_interval,
                total_events=total,
                success_count=success_count,
                failure_count=failure_count,
                discarded_stale_count=discarded_count,
                failure_rate=round(failure_count / total, 4),
                server_to_display_median_ms=s2d_med,
                server_to_display_p95_ms=s2d_p95,
                request_to_display_median_ms=r2d_med,
                request_to_display_p95_ms=r2d_p95,
                end_to_end_freshness_median_ms=fresh_med,
                end_to_end_freshness_p95_ms=fresh_p95,
                meets_demo_2s_target=(s2d_p95 < 2000.0 if s2d_p95 is not None else None),
            )
        )

    return TelemetrySummaryDto(total_events=len(events), groups=groups)
