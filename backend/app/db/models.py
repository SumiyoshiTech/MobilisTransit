"""SQLAlchemy 2.0 ORM tables for MobilisTransit (ARCHITECTURE.md §4.4).

Implements all 8 relational tables compatible with SQLite (WAL + FKs) and PostgreSQL,
containing strictly zero user GPS/location or camera frame columns (`CON-008`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, UTCDateTime, utc_now


class RouteModel(Base):
    """Configured transit route metadata (e.g., `LINE-9`)."""

    __tablename__ = "routes"

    route_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    route_code: Mapped[str] = mapped_column(String(32), nullable=False)
    route_name: Mapped[str] = mapped_column(String(255), nullable=False)
    network_version: Mapped[str] = mapped_column(String(64), nullable=False)
    calibration_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    stops: Mapped[list[StopModel]] = relationship(
        back_populates="route",
        order_by="StopModel.sequence_index",
        cascade="all, delete-orphan",
    )


class StopModel(Base):
    """Ordered stop topology, normalized geometry, reference offset, and AR marker metadata."""

    __tablename__ = "stops"
    __table_args__ = (
        UniqueConstraint("route_id", "sequence_index", name="uq_stops_route_sequence"),
        CheckConstraint("sequence_index >= 0", name="stop_sequence_non_negative"),
        CheckConstraint("distance_from_start_m >= 0.0", name="stop_distance_non_negative"),
        CheckConstraint("x_norm >= 0.0 AND x_norm <= 1.0", name="stop_x_norm_unit"),
        CheckConstraint("y_norm >= 0.0 AND y_norm <= 1.0", name="stop_y_norm_unit"),
    )

    route_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("routes.route_id", ondelete="CASCADE"),
        primary_key=True,
    )
    stop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sumo_bus_stop_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_from_start_m: Mapped[float] = mapped_column(Float, nullable=False)
    x_norm: Mapped[float] = mapped_column(Float, nullable=False)
    y_norm: Mapped[float] = mapped_column(Float, nullable=False)
    reference_offset_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ar_marker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ar_marker_width_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    route: Mapped[RouteModel] = relationship(back_populates="stops")


class JourneyModel(Base):
    """Persisted simulated or recorded bus journey with full scenario metadata (`REQ-003`)."""

    __tablename__ = "journeys"
    __table_args__ = (
        CheckConstraint("stop_hold_s >= 0 AND stop_hold_s <= 900", name="journey_hold_bounds"),
        CheckConstraint(
            "traffic_multiplier >= 0.5 AND traffic_multiplier <= 2.5",
            name="journey_traffic_bounds",
        ),
        CheckConstraint("departure_time_s >= 0", name="journey_depart_non_negative"),
        CheckConstraint("random_seed >= 0", name="journey_seed_non_negative"),
    )

    journey_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    route_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("routes.route_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    scenario_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    execution_id: Mapped[str] = mapped_column(String(128), nullable=False)
    experiment_family_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    network_version: Mapped[str] = mapped_column(String(64), nullable=False)
    demand_profile: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    traffic_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    stop_hold_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    intervention_stop_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    departure_time_s: Mapped[int] = mapped_column(Integer, nullable=False)
    random_seed: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="COMPLETED")
    playback_state: Mapped[str] = mapped_column(String(32), nullable=False, default="STOPPED")
    sim_duration_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_cleanly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    held_out_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        index=True,
    )

    snapshots: Mapped[list[VehicleSnapshotModel]] = relationship(
        back_populates="journey",
        order_by="VehicleSnapshotModel.sequence_number",
        cascade="all, delete-orphan",
    )
    observed_arrivals: Mapped[list[ObservedStopArrivalModel]] = relationship(
        back_populates="journey",
        order_by="ObservedStopArrivalModel.sequence_index",
        cascade="all, delete-orphan",
    )


class VehicleSnapshotModel(Base):
    """Periodic causal vehicle state snapshot for a journey."""

    __tablename__ = "vehicle_snapshots"
    __table_args__ = (
        UniqueConstraint("journey_id", "sequence_number", name="uq_vehicle_snapshots_journey_seq"),
        Index("ix_vehicle_snapshots_journey_seq", "journey_id", "sequence_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journey_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("journeys.journey_id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    simulation_clock_s: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicle_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    edge_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lane_position_m: Mapped[float] = mapped_column(Float, nullable=False)
    distance_travelled_m: Mapped[float] = mapped_column(Float, nullable=False)
    speed_mps: Mapped[float] = mapped_column(Float, nullable=False)
    current_segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    last_visited_stop_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_stop_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ongoing_stop_elapsed_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_completed_stop_dwell_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_generated_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)

    journey: Mapped[JourneyModel] = relationship(back_populates="snapshots")


class ObservedStopArrivalModel(Base):
    """Ground-truth observed stop arrival timestamp and dwell duration (`BR-006`, `REQ-003`)."""

    __tablename__ = "observed_stop_arrivals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["route_id", "stop_id"],
            ["stops.route_id", "stops.stop_id"],
            ondelete="RESTRICT",
            name="fk_observed_arrivals_route_stop",
        ),
        UniqueConstraint("journey_id", "stop_id", name="uq_observed_arrivals_journey_stop"),
        Index("ix_observed_arrivals_journey_stop", "journey_id", "stop_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journey_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("journeys.journey_id", ondelete="CASCADE"),
        nullable=False,
    )
    route_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stop_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    sumo_bus_stop_id: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_arrival_s: Mapped[int] = mapped_column(Integer, nullable=False)
    dwell_duration_s: Mapped[int] = mapped_column(Integer, nullable=False)

    journey: Mapped[JourneyModel] = relationship(back_populates="observed_arrivals")


class ExperimentJobModel(Base):
    """Paired baseline-vs-perturbed experiment job lifecycle and deduplication record."""

    __tablename__ = "experiment_jobs"
    __table_args__ = (
        Index("ix_experiment_jobs_idempotency_hash", "idempotency_hash"),
        Index(
            "uq_experiment_jobs_active_idempotency",
            "idempotency_hash",
            unique=True,
            sqlite_where=text("status IN ('QUEUED', 'RUNNING')"),
            postgresql_where=text("status IN ('QUEUED', 'RUNNING')"),
        ),
        CheckConstraint("stop_hold_s >= 0 AND stop_hold_s <= 900", name="exp_job_hold_bounds"),
        CheckConstraint(
            "traffic_multiplier >= 0.5 AND traffic_multiplier <= 2.5",
            name="exp_job_traffic_bounds",
        ),
    )

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    route_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("routes.route_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    demand_profile: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    departure_time_s: Mapped[int] = mapped_column(Integer, nullable=False)
    random_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    intervention_stop_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stop_hold_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    traffic_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    idempotency_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED", index=True)
    baseline_experiment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    perturbed_experiment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    runs: Mapped[list[ExperimentRunModel]] = relationship(
        back_populates="job",
        order_by="ExperimentRunModel.seq_id",
        cascade="all, delete-orphan",
    )


class ExperimentRunModel(Base):
    """Single BASELINE or PERTURBED experiment run with atomic sequential `EXP-xxx` ID."""

    __tablename__ = "experiment_runs"
    __table_args__ = (
        UniqueConstraint("job_id", "role", name="uq_experiment_runs_job_role"),
        Index("ix_experiment_runs_experiment_id", "experiment_id", unique=True),
        {"sqlite_autoincrement": True},
    )

    seq_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    job_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("experiment_jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    journey_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("journeys.journey_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    route_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("routes.route_id", ondelete="RESTRICT"),
        nullable=False,
    )
    network_version: Mapped[str] = mapped_column(String(64), nullable=False)
    demand_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    departure_time_s: Mapped[int] = mapped_column(Integer, nullable=False)
    random_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    intervention_stop_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stop_hold_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    traffic_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    job: Mapped[ExperimentJobModel] = relationship(back_populates="runs")
    journey: Mapped[JourneyModel | None] = relationship()


class TelemetryEventModel(Base):
    """Correlated wall-clock latency & synchronization telemetry record (`REQ-016`, `CON-008`)."""

    __tablename__ = "telemetry_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journey_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    scenario_id: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transport_mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    poll_interval_s: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    worker_generated_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    prediction_completed_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    server_published_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    client_request_start_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    client_received_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    client_rendered_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    clock_offset_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OK", index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        index=True,
    )
