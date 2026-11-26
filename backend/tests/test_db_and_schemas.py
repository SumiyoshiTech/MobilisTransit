"""Unit and persistence tests for T003: settings, Pydantic v2 contracts, DB schema & repositories."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from backend.app.db import (
    Base,
    DomainValidationError,
    EntityNotFoundError,
    ExperimentJobModel,
    ExperimentRunModel,
    JourneyModel,
    ObservedStopArrivalModel,
    compare_experiment_runs,
    create_db_engine,
    create_experiment_job,
    create_session_factory,
    export_journeys_for_ml,
    get_experiment_detail,
    get_journey,
    get_journey_snapshots,
    get_route_stops,
    init_db,
    list_experiments,
    list_journeys,
    list_routes,
    save_completed_journey,
    save_telemetry_events,
    seed_route_from_manifest,
    session_scope,
    stop_exists_on_route,
    summarize_telemetry,
    update_experiment_job_status,
)
from backend.app.db.repositories import (
    ML_ARRIVALS_COLUMNS,
    ML_JOURNEYS_COLUMNS,
    ML_SNAPSHOTS_COLUMNS,
)
from backend.app.schemas import (
    ErrorEnvelope,
    ExperimentCreateRequest,
    JourneySnapshotDto,
    ObservedStopComparisonRowDto,
    PlaybackControlRequest,
    StopPredictionDto,
    TelemetryEventIn,
    VehicleStateDto,
)
from backend.app.settings import Settings, ensure_sqlite_parent_dir
from backend.app.simulation.manifest_loader import (
    DEFAULT_CANONICAL_NETWORK_DIR,
    DEFAULT_FIXTURE_NETWORK_DIR,
    load_and_validate_manifest,
)
from backend.app.simulation.traci_runner import (
    ObservedStopArrival,
    RawVehicleState,
    SimulationRunConfig,
    SimulationRunResult,
)


def _make_sample_run_result(
    *,
    journey_id: str = "J-TEST-001",
    scenario_id: str = "SCEN-BASE",
    execution_id: str = "EXEC-001",
    demand_profile: str = "campus_rush",
    departure_time_s: int = 30,
    random_seed: int = 42,
    intervention_stop_id: str | None = None,
    stop_hold_s: int = 0,
    traffic_multiplier: float = 1.0,
    arrival_offsets: tuple[int, int, int, int] = (35, 110, 190, 275),
    dwell_durations: tuple[int, int, int, int] = (10, 10, 10, 10),
    completed_cleanly: bool = True,
    stop_ids: tuple[str, ...] = ("P01", "P02", "P03", "P04"),
) -> SimulationRunResult:
    cfg = SimulationRunConfig(
        network_dir=DEFAULT_FIXTURE_NETWORK_DIR,
        journey_id=journey_id,
        scenario_id=scenario_id,
        execution_id=execution_id,
        demand_profile=demand_profile,  # type: ignore[arg-type]
        departure_time_s=departure_time_s,
        random_seed=random_seed,
        intervention_stop_id=intervention_stop_id,
        stop_hold_s=stop_hold_s,
        traffic_multiplier=traffic_multiplier,
    )
    snapshots = [
        RawVehicleState(
            journey_id=journey_id,
            route_id="LINE-9",
            scenario_id=scenario_id,
            execution_id=execution_id,
            sequence_number=idx + 1,
            simulation_clock_s=departure_time_s + idx * 10,
            worker_generated_at_ms=1_790_000_000_000 + idx * 1000,
            vehicle_id="bus_line9_0",
            status="COMPLETED" if idx == 4 else ("AT_STOP" if idx == 1 else "IN_TRANSIT"),
            edge_id=f"e{idx}",
            lane_position_m=float(idx * 50),
            distance_travelled_m=float(idx * 350),
            speed_mps=0.0 if idx in (1, 4) else 8.5,
            current_segment_index=min(idx, 3),
            last_visited_stop_id="P01" if idx >= 1 else None,
            next_stop_id="P02" if idx < 3 else None,
            ongoing_stop_elapsed_s=5 if idx == 1 else 0,
            last_completed_stop_dwell_s=10 if idx >= 2 else 0,
            demand_profile=cfg.demand_profile,
        )
        for idx in range(5)
    ]
    arrivals = [
        ObservedStopArrival(
            stop_id=sid,
            sequence_index=seq,
            sumo_bus_stop_id=f"bs_{sid.lower()}",
            observed_arrival_s=arrival_offsets[seq],
            dwell_duration_s=dwell_durations[seq],
        )
        for seq, sid in enumerate(stop_ids)
    ]
    return SimulationRunResult(
        config=cfg,
        network_version="sample-line9-v1.0",
        raw_snapshots=snapshots,
        observed_arrivals=arrivals,
        completed_cleanly=completed_cleanly,
        sim_duration_s=max(0, arrival_offsets[-1] - departure_time_s),
    )


class TestSettings:
    def test_settings_defaults_and_sqlite_parent_dir_creation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        nested_db = tmp_path / "nested_data" / "sub" / "mobilis.db"
        assert not nested_db.parent.exists()

        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{nested_db.as_posix()}")
        monkeypatch.setenv("SUMO_NETWORK_DIR", "   ")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-do-not-log")

        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert nested_db.parent.is_dir()
        assert settings.sumo_network_dir is None
        assert settings.active_model_pointer == Path("ml/artifacts/active_model.json")
        assert settings.stop_hold_min_s == 0
        assert settings.stop_hold_max_s == 900
        assert settings.traffic_multiplier_min == 0.5
        assert settings.traffic_multiplier_max == 2.5
        assert settings.max_concurrent_sumo_jobs == 1
        assert settings.llm_provider == "ollama"
        assert settings.llm_model == "qwen2.5:3b"
        assert settings.ollama_base_url == "http://localhost:11434"
        assert settings.openai_api_base is None
        assert settings.openai_api_key == "sk-secret-do-not-log"
        # API key must never leak into repr/logs
        assert "sk-secret-do-not-log" not in repr(settings)

    def test_in_memory_and_postgres_urls_skip_dir_creation(self) -> None:
        assert ensure_sqlite_parent_dir("sqlite:///:memory:") is None
        assert ensure_sqlite_parent_dir("sqlite://") is None
        assert ensure_sqlite_parent_dir("postgresql+psycopg://user:pw@localhost/db") is None

    def test_settings_rejects_inverted_bounds(self) -> None:
        with pytest.raises(ValidationError, match="stop_hold_min_s"):
            Settings(
                _env_file=None,  # type: ignore[call-arg]
                database_url="sqlite:///:memory:",
                stop_hold_min_s=500,
                stop_hold_max_s=100,
            )
        with pytest.raises(ValidationError, match="traffic_multiplier_min"):
            Settings(
                _env_file=None,  # type: ignore[call-arg]
                database_url="sqlite:///:memory:",
                traffic_multiplier_min=2.0,
                traffic_multiplier_max=1.0,
            )


class TestPydanticContracts:
    def test_stop_prediction_dto_enforces_br001_and_br002(self) -> None:
        # Valid available prediction with reference timetable (delayed)
        delayed = StopPredictionDto(
            stop_id="P02",
            sequence_index=1,
            prediction_available=True,
            remaining_time_s=95,
            predicted_arrival_s=155,
            predicted_arrival_iso="00:02:35",
            reference_timetable_s=120,
            predicted_delay_s=35,
        )
        assert delayed.predicted_delay_s == 35

        # Valid early prediction (negative delay)
        early = StopPredictionDto(
            stop_id="P02",
            sequence_index=1,
            prediction_available=True,
            remaining_time_s=50,
            predicted_arrival_s=110,
            reference_timetable_s=120,
            predicted_delay_s=-10,
        )
        assert early.predicted_delay_s == -10

        # Valid prediction without reference timetable (BR-001: delay MUST be None)
        no_ref = StopPredictionDto(
            stop_id="P04",
            sequence_index=3,
            prediction_available=True,
            remaining_time_s=240,
            predicted_arrival_s=300,
            reference_timetable_s=None,
            predicted_delay_s=None,
        )
        assert no_ref.predicted_delay_s is None

        # Valid unavailable prediction (BR-002: all predicted fields None; reference may exist)
        unavailable = StopPredictionDto(
            stop_id="P03",
            sequence_index=2,
            prediction_available=False,
            remaining_time_s=None,
            predicted_arrival_s=None,
            predicted_arrival_iso=None,
            reference_timetable_s=180,
            predicted_delay_s=None,
            unavailable_reason="MODEL_UNAVAILABLE",
        )
        assert unavailable.prediction_available is False

        # BR-001 violation: non-null delay when reference_timetable_s is None
        with pytest.raises(ValidationError, match="BR-001"):
            StopPredictionDto(
                stop_id="P04",
                sequence_index=3,
                prediction_available=True,
                remaining_time_s=240,
                predicted_arrival_s=300,
                reference_timetable_s=None,
                predicted_delay_s=0,
            )

        # BR-001 violation: predicted_delay_s != predicted_arrival_s - reference_timetable_s
        with pytest.raises(ValidationError, match="BR-001"):
            StopPredictionDto(
                stop_id="P02",
                sequence_index=1,
                prediction_available=True,
                remaining_time_s=95,
                predicted_arrival_s=155,
                reference_timetable_s=120,
                predicted_delay_s=20,
            )

        # BR-001 violation: reference present but predicted_delay_s omitted
        with pytest.raises(ValidationError, match="BR-001"):
            StopPredictionDto(
                stop_id="P02",
                sequence_index=1,
                prediction_available=True,
                remaining_time_s=95,
                predicted_arrival_s=155,
                reference_timetable_s=120,
                predicted_delay_s=None,
            )

        # BR-002 violation: prediction_available=False with any non-null predicted field
        for bad_field, bad_val in (
            ("remaining_time_s", 30),
            ("predicted_arrival_s", 120),
            ("predicted_arrival_iso", "00:02:00"),
            ("predicted_delay_s", 15),
        ):
            with pytest.raises(ValidationError, match="BR-002"):
                StopPredictionDto(
                    stop_id="P02",
                    sequence_index=1,
                    prediction_available=False,
                    **{bad_field: bad_val},  # type: ignore[arg-type]
                )

        # Missing required time fields when prediction_available=True
        with pytest.raises(ValidationError, match="remaining_time_s"):
            StopPredictionDto(
                stop_id="P02",
                sequence_index=1,
                prediction_available=True,
                remaining_time_s=None,
                predicted_arrival_s=120,
            )

    @pytest.mark.parametrize(
        "overrides",
        [
            {"name": "   "},
            {"name": "x" * 121},
            {"stop_hold_s": -1, "intervention_stop_id": "P02"},
            {"stop_hold_s": 901, "intervention_stop_id": "P02"},
            {"stop_hold_s": 12.5, "intervention_stop_id": "P02"},
            {"stop_hold_s": True, "intervention_stop_id": "P02"},
            {"traffic_multiplier": 0.0},
            {"traffic_multiplier": 0.49},
            {"traffic_multiplier": 2.51},
            {"traffic_multiplier": 5.0},
            {"traffic_multiplier": float("nan")},
            {"traffic_multiplier": float("inf")},
            {"traffic_multiplier": True},
            {"stop_hold_s": 60, "intervention_stop_id": None},
            {"stop_hold_s": 60, "intervention_stop_id": "   "},
            {"stop_hold_s": 0, "traffic_multiplier": 1.0},  # zero intervention without opt-in
            {"departure_time_s": -1, "stop_hold_s": 60, "intervention_stop_id": "P02"},
            {"random_seed": 1.5, "stop_hold_s": 60, "intervention_stop_id": "P02"},
        ],
    )
    def test_experiment_create_request_enforces_br007(self, overrides: dict[str, Any]) -> None:
        payload: dict[str, Any] = {
            "name": "Morning hold experiment",
            "route_id": "LINE-9",
            "intervention_stop_id": "P02",
            "stop_hold_s": 0,
            "traffic_multiplier": 1.0,
        }
        payload.update(overrides)
        with pytest.raises(ValidationError):
            ExperimentCreateRequest(**payload)

    def test_experiment_create_request_accepts_valid_variants(self) -> None:
        hold_only = ExperimentCreateRequest(
            name="  Hold at P02  ",
            intervention_stop_id=" P02 ",
            stop_hold_s=180,
        )
        assert hold_only.name == "Hold at P02"
        assert hold_only.intervention_stop_id == "P02"

        traffic_only = ExperimentCreateRequest(
            name="Heavy traffic 1.8x",
            stop_hold_s=0,
            traffic_multiplier=1.8,
        )
        assert traffic_only.intervention_stop_id is None
        assert traffic_only.traffic_multiplier == 1.8

        zero_allowed = ExperimentCreateRequest(
            name="Explicit baseline check",
            stop_hold_s=0,
            traffic_multiplier=1.0,
            allow_zero_intervention=True,
        )
        assert zero_allowed.allow_zero_intervention is True

    def test_other_canonical_dtos_and_extra_forbid(self) -> None:
        snap = JourneySnapshotDto(
            source="SIMULATION",
            journey_id="J-001",
            route_id="LINE-9",
            scenario_id="SCEN-BASE",
            execution_id="EXEC-1",
            sequence_number=1,
            simulation_clock_s=60,
            playback_state="PLAYING",
            demand_profile="campus_rush",
            model_version="heuristic-baseline-v0.1.0",
            worker_generated_at_ms=1_790_000_000_000,
            prediction_completed_at_ms=1_790_000_000_050,
            generation_time_ms=1_790_000_000_060,
            vehicle=VehicleStateDto(
                vehicle_id="bus_line9_0",
                status="IN_TRANSIT",
                edge_id="e0",
                lane_position_m=10.0,
                distance_travelled_m=10.0,
                speed_mps=8.0,
                current_segment_index=0,
            ),
            predictions=[],
        )
        assert snap.model_version == "heuristic-baseline-v0.1.0"

        # Extra fields (e.g., accidental location/camera PII) must be rejected (CON-008)
        with pytest.raises(ValidationError):
            TelemetryEventIn(
                journey_id="J-001",
                scenario_id="SCEN-BASE",
                execution_id="EXEC-1",
                transport_mode="POLLING",
                poll_interval_s=5,
                latitude=39.74,  # type: ignore[call-arg]
            )

        with pytest.raises(ValidationError):
            PlaybackControlRequest(action="step", steps=True)  # type: ignore[arg-type]

        with pytest.raises(ValidationError, match="observed_difference_s"):
            ObservedStopComparisonRowDto(
                stop_id="P02",
                stop_name="Stop 2",
                sequence_index=1,
                baseline_observed_arrival_s=100,
                perturbed_observed_arrival_s=280,
                observed_difference_s=50,
            )

        env = ErrorEnvelope(
            error_code="VALIDATION_ERROR",
            message="stop_hold_s out of range",
            details={"field": "stop_hold_s"},
        )
        assert env.error_code == "VALIDATION_ERROR"


class TestDatabaseEngineAndSchema:
    def test_initializes_8_tables_with_wal_foreign_keys_indexes_and_privacy(
        self, tmp_path: Path
    ) -> None:
        db_file = tmp_path / "wal_check" / "mobilis.db"
        engine = create_db_engine(f"sqlite:///{db_file.as_posix()}")
        try:
            init_db(engine)

            inspector = inspect(engine)
            table_names = set(inspector.get_table_names())
            expected_tables = {
                "routes",
                "stops",
                "journeys",
                "vehicle_snapshots",
                "observed_stop_arrivals",
                "experiment_jobs",
                "experiment_runs",
                "telemetry_events",
            }
            assert table_names == expected_tables

            with engine.connect() as conn:
                journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar_one()
                fk_enabled = conn.execute(text("PRAGMA foreign_keys")).scalar_one()
                assert str(journal_mode).lower() == "wal"
                assert int(fk_enabled) == 1

            # Verify required indexes exist
            snap_indexes = {idx["name"] for idx in inspector.get_indexes("vehicle_snapshots")}
            arr_indexes = {idx["name"] for idx in inspector.get_indexes("observed_stop_arrivals")}
            run_indexes = {idx["name"] for idx in inspector.get_indexes("experiment_runs")}
            job_indexes = {idx["name"] for idx in inspector.get_indexes("experiment_jobs")}
            assert "ix_vehicle_snapshots_journey_seq" in snap_indexes
            assert "ix_observed_arrivals_journey_stop" in arr_indexes
            assert "ix_experiment_runs_experiment_id" in run_indexes
            assert "ix_experiment_jobs_idempotency_hash" in job_indexes
            assert "uq_experiment_jobs_active_idempotency" in job_indexes

            # Privacy audit (CON-008): no location/GPS/camera/device-tracking columns anywhere
            forbidden_tokens = (
                "latitude",
                "longitude",
                "gps",
                "user_location",
                "camera",
                "frame_blob",
                "device_id",
            )
            for table in Base.metadata.tables.values():
                for col in table.columns:
                    col_name = col.name.lower()
                    for token in forbidden_tokens:
                        assert token not in col_name, (
                            f"Forbidden privacy column {table.name}.{col.name} violates CON-008."
                        )

            # Foreign key constraint actually blocks orphan journey inserts
            factory = create_session_factory(engine)
            with pytest.raises(IntegrityError), session_scope(factory) as session:
                session.add(
                    JourneyModel(
                        journey_id="J-ORPHAN",
                        route_id="NONEXISTENT-ROUTE",
                        scenario_id="SCEN",
                        execution_id="EXEC",
                        source="SIMULATION",
                        network_version="v1",
                        demand_profile="campus_rush",
                        departure_time_s=0,
                        random_seed=1,
                    )
                )
        finally:
            engine.dispose()


class TestPersistenceAndRepositories:
    def test_seed_route_idempotency_canonical_and_fixture_and_sqlite_restart(
        self, tmp_path: Path
    ) -> None:
        db_file = tmp_path / "restart" / "mobilis.db"
        db_url = f"sqlite:///{db_file.as_posix()}"

        fixture_manifest = load_and_validate_manifest(DEFAULT_FIXTURE_NETWORK_DIR)
        canonical_manifest = load_and_validate_manifest(DEFAULT_CANONICAL_NETWORK_DIR)

        engine1 = create_db_engine(db_url)
        try:
            init_db(engine1)
            factory1 = create_session_factory(engine1)

            with session_scope(factory1) as session:
                # Seed canonical 22-stop Line 9 with circular loop terminal gtfs_lra_0096
                seed_route_from_manifest(session, canonical_manifest)
                canon_stops = get_route_stops(session, "LINE-9")
                assert len(canon_stops) == 22
                assert canon_stops[0].sumo_bus_stop_id == "gtfs_lra_0096"
                assert canon_stops[-1].sumo_bus_stop_id == "gtfs_lra_0096"

                # Re-seed idempotently with 4-stop fixture (no arrivals yet -> obsolete cleaned)
                seed_route_from_manifest(session, fixture_manifest)
                seed_route_from_manifest(session, fixture_manifest)
                routes = list_routes(session)
                assert len(routes) == 1
                assert routes[0].stop_count == 4
                stops = get_route_stops(session, "LINE-9")
                assert [s.stop_id for s in stops] == ["P01", "P02", "P03", "P04"]
                assert stops[3].reference_offset_s is None
                assert stop_exists_on_route(session, "LINE-9", "P02") is True
                assert stop_exists_on_route(session, "LINE-9", "P99") is False

                # Persist a completed journey and create a paired experiment job
                run_res = _make_sample_run_result(journey_id="J-PERSIST-001")
                save_completed_journey(
                    session,
                    run_res,
                    route_id="LINE-9",
                    source="SIMULATION",
                    experiment_family_id="FAM-001",
                )
                job, deduplicated = create_experiment_job(
                    session,
                    ExperimentCreateRequest(
                        name="Persist check",
                        route_id="LINE-9",
                        intervention_stop_id="P02",
                        stop_hold_s=180,
                    ),
                )
                assert deduplicated is False
                assert job.baseline_experiment_id == "EXP-001"
                assert job.perturbed_experiment_id == "EXP-002"
        finally:
            engine1.dispose()

        # Reopen fresh engine after full dispose (simulates server restart, REQ-003)
        engine2 = create_db_engine(db_url)
        try:
            factory2 = create_session_factory(engine2)
            with session_scope(factory2) as session:
                assert len(get_route_stops(session, "LINE-9")) == 4
                journey = get_journey(session, "J-PERSIST-001")
                assert journey is not None
                assert journey.experiment_family_id == "FAM-001"
                assert len(get_journey_snapshots(session, "J-PERSIST-001")) == 5

                summaries = list_journeys(session, route_id="LINE-9", source="SIMULATION")
                assert [j.journey_id for j in summaries] == ["J-PERSIST-001"]

                detail = get_experiment_detail(session, "EXP-002")
                assert detail.baseline_experiment_id == "EXP-001"
                assert detail.perturbed_experiment_id == "EXP-002"
                assert detail.stop_hold_s == 180
        finally:
            engine2.dispose()

    def test_save_completed_journey_is_atomic_on_invalid_stop_fk(self) -> None:
        engine = create_db_engine("sqlite:///:memory:")
        try:
            init_db(engine)
            factory = create_session_factory(engine)
            manifest = load_and_validate_manifest(DEFAULT_FIXTURE_NETWORK_DIR)

            with session_scope(factory) as session:
                seed_route_from_manifest(session, manifest)
                bad_run = _make_sample_run_result(
                    journey_id="J-ROLLBACK",
                    stop_ids=("P01", "P02", "P03", "P99_INVALID"),
                )
                with pytest.raises(IntegrityError):
                    save_completed_journey(session, bad_run, route_id="LINE-9")

                # Savepoint rolled back cleanly — zero orphan journey or arrival rows
                assert get_journey(session, "J-ROLLBACK") is None
                arr_count = session.scalar(
                    select(func.count()).select_from(ObservedStopArrivalModel)
                )
                assert arr_count == 0
        finally:
            engine.dispose()

    def test_experiment_deduplication_sequential_ids_filtering_and_observed_comparison(
        self,
    ) -> None:
        engine = create_db_engine("sqlite:///:memory:")
        try:
            init_db(engine)
            factory = create_session_factory(engine)
            manifest = load_and_validate_manifest(DEFAULT_FIXTURE_NETWORK_DIR)

            with session_scope(factory) as session:
                seed_route_from_manifest(session, manifest)

                # Invalid route or stop rejected before job creation (BR-007)
                with pytest.raises(EntityNotFoundError):
                    create_experiment_job(
                        session,
                        ExperimentCreateRequest(
                            name="Bad route",
                            route_id="LINE-99",
                            intervention_stop_id="P02",
                            stop_hold_s=60,
                        ),
                    )
                with pytest.raises(DomainValidationError, match="BR-007"):
                    create_experiment_job(
                        session,
                        ExperimentCreateRequest(
                            name="Bad stop",
                            route_id="LINE-9",
                            intervention_stop_id="P99",
                            stop_hold_s=60,
                        ),
                    )

                req1 = ExperimentCreateRequest(
                    name="Hold 180s at P02",
                    route_id="LINE-9",
                    intervention_stop_id="P02",
                    stop_hold_s=180,
                    traffic_multiplier=1.0,
                    demand_profile="campus_rush",
                    departure_time_s=30,
                    random_seed=42,
                )
                job1, dedup1 = create_experiment_job(session, req1)
                assert dedup1 is False
                assert job1.baseline_experiment_id == "EXP-001"
                assert job1.perturbed_experiment_id == "EXP-002"

                # Identical active QUEUED submission is deduplicated (BR-008)
                job1_dup, dedup2 = create_experiment_job(session, req1)
                assert dedup2 is True
                assert job1_dup.job_id == job1.job_id

                # Still deduplicated while RUNNING
                update_experiment_job_status(session, job1.job_id, "RUNNING")
                job1_dup_running, dedup3 = create_experiment_job(session, req1)
                assert dedup3 is True
                assert job1_dup_running.job_id == job1.job_id

                # Cannot compare before journeys are linked
                with pytest.raises(DomainValidationError, match="no completed journey"):
                    compare_experiment_runs(session, "EXP-001", "EXP-002")

                # Persist paired baseline and perturbed journeys and complete job1
                base_run = _make_sample_run_result(
                    journey_id="J-EXP-001",
                    scenario_id="EXP-001",
                    stop_hold_s=0,
                    arrival_offsets=(35, 110, 190, 275),
                    dwell_durations=(10, 10, 10, 10),
                )
                pert_run = _make_sample_run_result(
                    journey_id="J-EXP-002",
                    scenario_id="EXP-002",
                    intervention_stop_id="P02",
                    stop_hold_s=180,
                    arrival_offsets=(35, 110, 372, 460),
                    dwell_durations=(10, 190, 10, 10),
                )
                save_completed_journey(
                    session, base_run, route_id="LINE-9", experiment_family_id=job1.job_id
                )
                save_completed_journey(
                    session, pert_run, route_id="LINE-9", experiment_family_id=job1.job_id
                )
                update_experiment_job_status(
                    session,
                    job1.job_id,
                    "COMPLETED",
                    baseline_journey_id="J-EXP-001",
                    perturbed_journey_id="J-EXP-002",
                )

                # Observed stop-by-stop comparison (BR-005 & BR-006)
                cmp = compare_experiment_runs(session, "EXP-001", "EXP-002")
                assert cmp.data_origin == "OBSERVED_SIMULATED_ARRIVALS"
                assert cmp.invariants_match is True
                assert cmp.invariants.random_seed == 42
                assert [(r.stop_id, r.observed_difference_s) for r in cmp.rows] == [
                    ("P01", 0),
                    ("P02", 0),
                    ("P03", 182),
                    ("P04", 185),
                ]
                assert cmp.rows[1].perturbed_dwell_s == 190
                assert cmp.max_observed_difference_s == 185
                assert cmp.mean_observed_difference_s == round((0 + 0 + 182 + 185) / 4, 2)

                # Detail lookup works by BOTH job_id and EXP-xxx and embeds comparison
                detail_by_job = get_experiment_detail(session, job1.job_id)
                detail_by_exp = get_experiment_detail(session, "EXP-001")
                assert detail_by_job.job_id == detail_by_exp.job_id
                assert detail_by_exp.comparison is not None
                assert detail_by_exp.comparison.max_observed_difference_s == 185

                # Once COMPLETED, a new job with same params or different params allocates EXP-003/004
                req2 = ExperimentCreateRequest(
                    name="Heavy congestion 1.8x",
                    route_id="LINE-9",
                    stop_hold_s=0,
                    traffic_multiplier=1.8,
                    demand_profile="heavy_congestion",
                    departure_time_s=60,
                    random_seed=99,
                )
                job2, dedup4 = create_experiment_job(session, req2)
                assert dedup4 is False
                assert job2.baseline_experiment_id == "EXP-003"
                assert job2.perturbed_experiment_id == "EXP-004"

                # Comparing experiments with differing seeds flags invariants_match=False (BR-005)
                mismatch_run = _make_sample_run_result(
                    journey_id="J-EXP-004",
                    scenario_id="EXP-004",
                    demand_profile="heavy_congestion",
                    departure_time_s=60,
                    random_seed=99,
                    traffic_multiplier=1.8,
                    arrival_offsets=(65, 160, 260, 360),
                )
                save_completed_journey(
                    session, mismatch_run, route_id="LINE-9", experiment_family_id=job2.job_id
                )
                update_experiment_job_status(
                    session,
                    job2.job_id,
                    "RUNNING",
                    perturbed_journey_id="J-EXP-004",
                )
                mismatched_cmp = compare_experiment_runs(session, "EXP-001", "EXP-004")
                assert mismatched_cmp.invariants_match is False
                with pytest.raises(EntityNotFoundError):
                    compare_experiment_runs(session, "EXP-999", "EXP-002")

                # Adjust created_at on job1 to test UTC/offset time window filtering
                t_old = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
                t_new = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
                session.get(ExperimentJobModel, job1.job_id).created_at = t_old  # type: ignore[union-attr]
                session.get(ExperimentJobModel, job2.job_id).created_at = t_new  # type: ignore[union-attr]
                session.flush()

                # Filtering across all parameters
                assert [e.job_id for e in list_experiments(session, status="COMPLETED")] == [
                    job1.job_id
                ]
                assert [
                    e.job_id for e in list_experiments(session, demand_profile="heavy_congestion")
                ] == [job2.job_id]
                assert [
                    e.job_id for e in list_experiments(session, min_hold_s=100, max_hold_s=300)
                ] == [job1.job_id]
                assert [
                    e.job_id
                    for e in list_experiments(
                        session, min_traffic_multiplier=1.5, max_traffic_multiplier=2.0
                    )
                ] == [job2.job_id]
                # Test offset-aware datetime (+02:00) normalized cleanly to UTC
                lisbon_plus2 = timezone(timedelta(hours=2))
                filtered_time = list_experiments(
                    session,
                    from_time=datetime(2026, 11, 24, 2, 0, tzinfo=lisbon_plus2),
                    to_time=datetime(2026, 11, 26, 2, 0, tzinfo=lisbon_plus2),
                )
                assert [e.job_id for e in filtered_time] == [job2.job_id]
        finally:
            engine.dispose()

    def test_concurrent_experiment_creation_produces_unique_sequential_exp_ids(
        self, tmp_path: Path
    ) -> None:
        db_file = tmp_path / "concurrent" / "mobilis.db"
        engine = create_db_engine(f"sqlite:///{db_file.as_posix()}")
        try:
            init_db(engine)
            factory = create_session_factory(engine)
            manifest = load_and_validate_manifest(DEFAULT_FIXTURE_NETWORK_DIR)

            with session_scope(factory) as session:
                seed_route_from_manifest(session, manifest)

            def _worker(seed: int) -> tuple[str, str]:
                with session_scope(factory) as session:
                    job, dedup = create_experiment_job(
                        session,
                        ExperimentCreateRequest(
                            name=f"Concurrent seed {seed}",
                            route_id="LINE-9",
                            intervention_stop_id="P02",
                            stop_hold_s=60 + seed * 10,
                            random_seed=seed,
                        ),
                    )
                    assert dedup is False
                    assert job.baseline_experiment_id is not None
                    assert job.perturbed_experiment_id is not None
                    return job.baseline_experiment_id, job.perturbed_experiment_id

            with ThreadPoolExecutor(max_workers=4) as pool:
                pairs = list(pool.map(_worker, range(1, 9)))

            all_ids = [exp_id for pair in pairs for exp_id in pair]
            assert len(all_ids) == 16
            assert len(set(all_ids)) == 16
            assert sorted(all_ids) == [f"EXP-{i:03d}" for i in range(1, 17)]
        finally:
            engine.dispose()

    def test_concurrent_identical_experiment_submissions_deduplicate_atomically(
        self, tmp_path: Path
    ) -> None:
        """Verify BR-008 under synchronized concurrent identical submissions on SQLite WAL."""
        db_file = tmp_path / "concurrent_dedup" / "mobilis.db"
        engine = create_db_engine(f"sqlite:///{db_file.as_posix()}")
        try:
            init_db(engine)
            factory = create_session_factory(engine)
            manifest = load_and_validate_manifest(DEFAULT_FIXTURE_NETWORK_DIR)

            with session_scope(factory) as session:
                seed_route_from_manifest(session, manifest)

            worker_count = 8
            barrier = threading.Barrier(worker_count)

            def _submit_identical(_idx: int) -> tuple[str, bool]:
                barrier.wait()
                with session_scope(factory) as session:
                    job, dedup = create_experiment_job(
                        session,
                        ExperimentCreateRequest(
                            name="Simultaneous P02 Hold",
                            route_id="LINE-9",
                            intervention_stop_id="P02",
                            stop_hold_s=180,
                            demand_profile="campus_rush",
                            departure_time_s=30,
                            random_seed=42,
                        ),
                    )
                    return job.job_id, dedup

            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                results = list(pool.map(_submit_identical, range(worker_count)))

            job_ids = {job_id for job_id, _ in results}
            created_flags = [dedup for _, dedup in results]
            assert len(job_ids) == 1
            assert created_flags.count(False) == 1
            assert created_flags.count(True) == worker_count - 1

            winning_job_id = next(iter(job_ids))
            with session_scope(factory) as session:
                total_jobs = session.scalar(select(func.count()).select_from(ExperimentJobModel))
                total_runs = session.scalar(select(func.count()).select_from(ExperimentRunModel))
                run_ids = list(
                    session.scalars(
                        select(ExperimentRunModel.experiment_id).order_by(ExperimentRunModel.seq_id)
                    ).all()
                )
                assert total_jobs == 1
                assert total_runs == 2
                assert run_ids == ["EXP-001", "EXP-002"]

                # Partial unique index allows re-submission once the prior job completes or fails
                update_experiment_job_status(session, winning_job_id, "COMPLETED")
                re_job, re_dedup = create_experiment_job(
                    session,
                    ExperimentCreateRequest(
                        name="Simultaneous P02 Hold",
                        route_id="LINE-9",
                        intervention_stop_id="P02",
                        stop_hold_s=180,
                        demand_profile="campus_rush",
                        departure_time_s=30,
                        random_seed=42,
                    ),
                )
                assert re_dedup is False
                assert re_job.job_id != winning_job_id
                assert re_job.baseline_experiment_id == "EXP-003"
                assert re_job.perturbed_experiment_id == "EXP-004"

                update_experiment_job_status(
                    session, re_job.job_id, "FAILED", error_message="Simulated SUMO timeout"
                )
                retry_job, retry_dedup = create_experiment_job(
                    session,
                    ExperimentCreateRequest(
                        name="Simultaneous P02 Hold",
                        route_id="LINE-9",
                        intervention_stop_id="P02",
                        stop_hold_s=180,
                        demand_profile="campus_rush",
                        departure_time_s=30,
                        random_seed=42,
                    ),
                )
                assert retry_dedup is False
                assert retry_job.baseline_experiment_id == "EXP-005"
                assert retry_job.perturbed_experiment_id == "EXP-006"
        finally:
            engine.dispose()


class TestMlExportAndTelemetry:
    def test_export_journeys_for_ml_empty_and_populated(self) -> None:
        engine = create_db_engine("sqlite:///:memory:")
        try:
            init_db(engine)
            factory = create_session_factory(engine)
            manifest = load_and_validate_manifest(DEFAULT_FIXTURE_NETWORK_DIR)

            with session_scope(factory) as session:
                seed_route_from_manifest(session, manifest)

                # Empty DB returns cleanly typed empty DataFrames with exact schemas
                j_empty, s_empty, a_empty = export_journeys_for_ml(session)
                assert list(j_empty.columns) == ML_JOURNEYS_COLUMNS
                assert list(s_empty.columns) == ML_SNAPSHOTS_COLUMNS
                assert list(a_empty.columns) == ML_ARRIVALS_COLUMNS
                assert len(j_empty) == len(s_empty) == len(a_empty) == 0

                # Persist 1 normal completed, 1 disrupted held-out completed, and 1 failed journey
                save_completed_journey(
                    session,
                    _make_sample_run_result(journey_id="J-ML-NORM", stop_hold_s=0),
                    route_id="LINE-9",
                    experiment_family_id="FAM-A",
                    held_out_candidate=False,
                )
                save_completed_journey(
                    session,
                    _make_sample_run_result(
                        journey_id="J-ML-DISP",
                        intervention_stop_id="P02",
                        stop_hold_s=120,
                        random_seed=77,
                    ),
                    route_id="LINE-9",
                    experiment_family_id="FAM-B",
                    held_out_candidate=True,
                )
                save_completed_journey(
                    session,
                    _make_sample_run_result(
                        journey_id="J-ML-FAIL",
                        completed_cleanly=False,
                    ),
                    route_id="LINE-9",
                    experiment_family_id="FAM-C",
                )

                journeys_df, snapshots_df, arrivals_df = export_journeys_for_ml(
                    session, route_id="LINE-9"
                )
                assert list(journeys_df["journey_id"]) == ["J-ML-NORM", "J-ML-DISP"]
                assert list(journeys_df["is_disrupted"]) == [False, True]
                assert list(journeys_df["held_out_candidate"]) == [False, True]
                assert len(snapshots_df) == 10
                assert len(arrivals_df) == 8
                # Snapshots sorted by sequence_number within each journey
                for _, grp in snapshots_df.groupby("journey_id"):
                    assert list(grp["sequence_number"]) == [1, 2, 3, 4, 5]

                # completed_only=False includes the failed journey
                all_j, _, _ = export_journeys_for_ml(session, completed_only=False)
                assert len(all_j) == 3
        finally:
            engine.dispose()

    def test_telemetry_save_and_summary_metrics(self) -> None:
        engine = create_db_engine("sqlite:///:memory:")
        try:
            init_db(engine)
            factory = create_session_factory(engine)

            with session_scope(factory) as session:
                empty_summary = summarize_telemetry(session)
                assert empty_summary.total_events == 0
                assert empty_summary.groups == []

                events = [
                    # SSE fast demo group (3 OK + 1 DISCARDED_STALE)
                    TelemetryEventIn(
                        journey_id="J-1",
                        scenario_id="S-1",
                        execution_id="E-1",
                        sequence_number=1,
                        transport_mode="LIVE_DEMO_STREAM",
                        poll_interval_s=None,
                        worker_generated_at_ms=1000,
                        prediction_completed_at_ms=1020,
                        server_published_at_ms=1030,
                        client_request_start_ms=1000,
                        client_received_at_ms=1100,
                        client_rendered_at_ms=1150,
                        clock_offset_ms=50,  # aligned rendered = 1200 -> s2d=170, fresh=200
                        status="OK",
                    ),
                    TelemetryEventIn(
                        journey_id="J-1",
                        scenario_id="S-1",
                        execution_id="E-1",
                        sequence_number=2,
                        transport_mode="LIVE_DEMO_STREAM",
                        poll_interval_s=None,
                        worker_generated_at_ms=2000,
                        prediction_completed_at_ms=2020,
                        server_published_at_ms=2030,
                        client_request_start_ms=2000,
                        client_received_at_ms=2150,
                        client_rendered_at_ms=2250,
                        clock_offset_ms=50,  # aligned rendered = 2300 -> s2d=270, fresh=300
                        status="OK",
                    ),
                    TelemetryEventIn(
                        journey_id="J-1",
                        scenario_id="S-1",
                        execution_id="E-1",
                        sequence_number=1,
                        transport_mode="LIVE_DEMO_STREAM",
                        poll_interval_s=None,
                        status="DISCARDED_STALE",
                    ),
                    # Polling 5s group (1 slow OK + 1 TIMEOUT)
                    TelemetryEventIn(
                        journey_id="J-1",
                        scenario_id="S-1",
                        execution_id="E-1",
                        sequence_number=3,
                        transport_mode="POLLING",
                        poll_interval_s=5,
                        worker_generated_at_ms=5000,
                        prediction_completed_at_ms=5050,
                        server_published_at_ms=5100,
                        client_request_start_ms=7300,
                        client_received_at_ms=7400,
                        client_rendered_at_ms=7500,
                        clock_offset_ms=0,  # s2d=2400, r2d=200, fresh=2500
                        status="OK",
                    ),
                    TelemetryEventIn(
                        journey_id="J-1",
                        scenario_id="S-1",
                        execution_id="E-1",
                        sequence_number=4,
                        transport_mode="POLLING",
                        poll_interval_s=5,
                        status="TIMEOUT",
                        error_code="HTTP_TIMEOUT",
                    ),
                ]
                inserted = save_telemetry_events(session, events)
                assert inserted == 5

                summary = summarize_telemetry(session)
                assert summary.total_events == 5
                assert len(summary.groups) == 2

                sse_group = next(
                    g for g in summary.groups if g.transport_mode == "LIVE_DEMO_STREAM"
                )
                assert sse_group.total_events == 3
                assert sse_group.success_count == 2
                assert sse_group.failure_count == 0
                assert sse_group.discarded_stale_count == 1
                assert sse_group.failure_rate == 0.0
                assert sse_group.server_to_display_median_ms == 220.0
                assert sse_group.end_to_end_freshness_median_ms == 250.0
                assert sse_group.meets_demo_2s_target is True

                poll_group = next(g for g in summary.groups if g.transport_mode == "POLLING")
                assert poll_group.poll_interval_s == 5
                assert poll_group.total_events == 2
                assert poll_group.failure_count == 1
                assert poll_group.failure_rate == 0.5
                assert poll_group.server_to_display_median_ms == 2400.0
                assert poll_group.request_to_display_median_ms == 200.0
                assert poll_group.end_to_end_freshness_median_ms == 2500.0
                assert poll_group.meets_demo_2s_target is False
        finally:
            engine.dispose()
