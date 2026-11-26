"""Canonical Pydantic v2 REST/JSON DTOs enforcing MobilisTransit business rules.

Enforces:
- BR-001: Predicted delay equals `predicted_arrival_s - reference_timetable_s`, and MUST be
  `None` whenever `reference_timetable_s` is `None`.
- BR-002: Missing/unavailable predictions MUST keep all predicted time/delay fields `None`.
- BR-005 / BR-006: Paired experiment invariants and observed simulated arrival comparisons
  are explicitly typed and distinguished from ML predictions.
- BR-007: Strict bounds and type validation for stop holds (`[0, 900]` integer), traffic
  multipliers (`[0.5, 2.5]` finite float), required `intervention_stop_id`, and zero-intervention
  rejection unless explicitly opted in.
- CON-008: Strict `extra="forbid"` schemas with zero user location or camera frame fields.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

DemandProfile = Literal["off_peak", "campus_rush", "heavy_congestion"]
VehicleStatus = Literal["IN_TRANSIT", "AT_STOP", "COMPLETED"]
SnapshotSource = Literal["SIMULATION", "RECORDED_REPLAY", "DEMONSTRATION"]
PlaybackState = Literal["PLAYING", "PAUSED", "STOPPED"]
PlaybackAction = Literal["play", "pause", "step", "restart"]
JourneyStatus = Literal["ACTIVE", "COMPLETED", "FAILED"]
ExperimentJobStatus = Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED"]
ExperimentRole = Literal["BASELINE", "PERTURBED"]
TransportMode = Literal["POLLING", "LIVE_DEMO_STREAM"]
TelemetryStatus = Literal["OK", "ERROR", "TIMEOUT", "DISCARDED_STALE", "DISCARDED_SCOPE"]

SIMULATION_DOMAIN_NOTICE = (
    "Simulation-based prototype data (Eclipse SUMO); not live Mobilis telemetry."
)
OBSERVED_COMPARISON_NOTICE = (
    "Observed simulated arrival times from paired Eclipse SUMO runs; "
    "distinct from ML predicted delays and not real-world operational measurements."
)


def _require_strict_int(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, float)):
        raise PydanticCustomError(
            "strict_int",
            "Field must be an integer, not a boolean or float.",
        )
    return value


def _require_finite_float(value: Any) -> Any:
    if isinstance(value, bool):
        raise PydanticCustomError(
            "strict_float",
            "Field must be a finite number, not a boolean.",
        )
    if isinstance(value, (int, float)) and not math.isfinite(float(value)):
        raise ValueError("Field must be a finite number.")
    return value


class RouteDto(BaseModel):
    """Route metadata returned by `GET /routes`."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    route_id: str = Field(min_length=1)
    route_code: str = Field(min_length=1)
    route_name: str = Field(min_length=1)
    network_version: str = Field(min_length=1)
    calibration_notes: str = ""
    stop_count: int = Field(default=0, ge=0)


class RouteStopDto(BaseModel):
    """Ordered stop topology, normalized geometry, reference offset, and AR marker metadata."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    route_id: str = Field(min_length=1)
    stop_id: str = Field(min_length=1)
    sumo_bus_stop_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    sequence_index: int = Field(ge=0)
    distance_from_start_m: float = Field(ge=0.0)
    x_norm: float = Field(ge=0.0, le=1.0)
    y_norm: float = Field(ge=0.0, le=1.0)
    reference_offset_s: int | None = Field(default=None, ge=0)
    ar_marker_id: str | None = None
    ar_marker_width_m: float | None = Field(default=None, gt=0.0)


class JourneySummaryDto(BaseModel):
    """Summary metadata for a stored or active journey (`GET /journeys`)."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    journey_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    experiment_family_id: str | None = None
    source: SnapshotSource
    network_version: str = Field(min_length=1)
    demand_profile: DemandProfile
    departure_time_s: int = Field(ge=0)
    random_seed: int = Field(ge=0)
    intervention_stop_id: str | None = None
    stop_hold_s: int = Field(default=0, ge=0, le=900)
    traffic_multiplier: float = Field(default=1.0, ge=0.5, le=2.5)
    status: JourneyStatus
    playback_state: PlaybackState
    created_at: datetime


class VehicleStateDto(BaseModel):
    """Vehicle kinematic and stop-tracking state embedded inside `JourneySnapshotDto`."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    vehicle_id: str = Field(min_length=1)
    status: VehicleStatus
    edge_id: str
    lane_position_m: float = Field(ge=0.0)
    distance_travelled_m: float = Field(ge=0.0)
    speed_mps: float = Field(ge=0.0)
    current_segment_index: int = Field(ge=0)
    last_visited_stop_id: str | None = None
    next_stop_id: str | None = None
    ongoing_stop_elapsed_s: int = Field(default=0, ge=0)
    last_completed_stop_dwell_s: int = Field(default=0, ge=0)


class StopPredictionDto(BaseModel):
    """Per-stop arrival prediction enforcing `BR-001` and `BR-002`."""

    model_config = ConfigDict(extra="forbid")

    stop_id: str = Field(min_length=1)
    stop_name: str | None = None
    sequence_index: int = Field(ge=0)
    visited: bool = False
    prediction_available: bool
    remaining_time_s: int | None = Field(default=None, ge=0)
    predicted_arrival_s: int | None = Field(default=None, ge=0)
    predicted_arrival_iso: str | None = None
    reference_timetable_s: int | None = Field(default=None, ge=0)
    predicted_delay_s: int | None = None
    unavailable_reason: str | None = None

    @field_validator(
        "sequence_index",
        "remaining_time_s",
        "predicted_arrival_s",
        "reference_timetable_s",
        "predicted_delay_s",
        mode="before",
    )
    @classmethod
    def _strict_ints(cls, value: Any) -> Any:
        return _require_strict_int(value)

    @model_validator(mode="after")
    def _enforce_prediction_rules(self) -> StopPredictionDto:
        if not self.prediction_available:
            non_null = [
                field_name
                for field_name in (
                    "remaining_time_s",
                    "predicted_arrival_s",
                    "predicted_arrival_iso",
                    "predicted_delay_s",
                )
                if getattr(self, field_name) is not None
            ]
            if non_null:
                raise ValueError(
                    "When prediction_available is False, prediction fields must all be None "
                    f"(BR-002); found non-null: {', '.join(non_null)}."
                )
            return self

        if self.remaining_time_s is None or self.predicted_arrival_s is None:
            raise ValueError(
                "remaining_time_s and predicted_arrival_s are required when "
                "prediction_available is True."
            )

        if self.reference_timetable_s is None:
            if self.predicted_delay_s is not None:
                raise ValueError(
                    "predicted_delay_s must be None when reference_timetable_s is None (BR-001)."
                )
        else:
            expected_delay = self.predicted_arrival_s - self.reference_timetable_s
            if self.predicted_delay_s != expected_delay:
                raise ValueError(
                    f"predicted_delay_s ({self.predicted_delay_s}) must equal "
                    f"predicted_arrival_s - reference_timetable_s ({expected_delay}) (BR-001)."
                )
        return self


class JourneySnapshotDto(BaseModel):
    """Canonical REST and SSE journey state + downstream prediction snapshot."""

    model_config = ConfigDict(extra="forbid")

    source: SnapshotSource
    journey_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    sequence_number: int = Field(ge=1)
    simulation_clock_s: int = Field(ge=0)
    playback_state: PlaybackState
    demand_profile: DemandProfile
    model_version: str = Field(min_length=1)
    worker_generated_at_ms: int = Field(ge=0)
    prediction_completed_at_ms: int = Field(ge=0)
    generation_time_ms: int = Field(ge=0)
    vehicle: VehicleStateDto
    predictions: list[StopPredictionDto] = Field(default_factory=list)
    simulation_domain_notice: str = SIMULATION_DOMAIN_NOTICE


class PlaybackControlRequest(BaseModel):
    """Command payload for `POST /journeys/{journey_id}/playback`."""

    model_config = ConfigDict(extra="forbid")

    action: PlaybackAction
    steps: int = Field(default=1, ge=1, le=300)
    auto_play: bool = True

    @field_validator("steps", mode="before")
    @classmethod
    def _strict_steps(cls, value: Any) -> Any:
        return _require_strict_int(value)


class ExperimentCreateRequest(BaseModel):
    """Validated request payload for `POST /experiments` enforcing `BR-007` and `DEC-002`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    route_id: str = Field(default="LINE-9", min_length=1)
    intervention_stop_id: str | None = None
    stop_hold_s: int = Field(default=0, ge=0, le=900)
    traffic_multiplier: float = Field(default=1.0, ge=0.5, le=2.5)
    demand_profile: DemandProfile = "campus_rush"
    departure_time_s: int = Field(default=25790, ge=0, le=86400)
    random_seed: int = Field(default=42, ge=0)
    idempotency_key: str | None = Field(default=None, max_length=128)
    allow_zero_intervention: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must be a non-empty string.")
        if len(stripped) > 120:
            raise ValueError("name must be at most 120 characters.")
        return stripped

    @field_validator("route_id")
    @classmethod
    def _validate_route_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("route_id must not be blank.")
        return stripped

    @field_validator("intervention_stop_id")
    @classmethod
    def _validate_intervention_stop_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("intervention_stop_id must not be blank when provided.")
        return stripped

    @field_validator("idempotency_key")
    @classmethod
    def _normalize_idempotency_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("stop_hold_s", "departure_time_s", "random_seed", mode="before")
    @classmethod
    def _strict_ints(cls, value: Any) -> Any:
        return _require_strict_int(value)

    @field_validator("traffic_multiplier", mode="before")
    @classmethod
    def _finite_multiplier(cls, value: Any) -> Any:
        return _require_finite_float(value)

    @model_validator(mode="after")
    def _validate_intervention_rules(self) -> ExperimentCreateRequest:
        if self.stop_hold_s > 0 and self.intervention_stop_id is None:
            raise ValueError(
                "intervention_stop_id is required whenever stop_hold_s > 0 (BR-007)."
            )

        is_zero_intervention = self.stop_hold_s == 0 and math.isclose(
            self.traffic_multiplier,
            1.0,
            abs_tol=1e-9,
        )
        if is_zero_intervention and not self.allow_zero_intervention:
            raise ValueError(
                "Zero-intervention experiment rejected: specify stop_hold_s > 0 or "
                "traffic_multiplier != 1.0, or set allow_zero_intervention=True (BR-007)."
            )
        return self


class ExperimentJobCreatedDto(BaseModel):
    """Response payload for `POST /experiments` (`202 Accepted` or deduplicated `200 OK`)."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    job_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: ExperimentJobStatus
    deduplicated: bool = False
    baseline_experiment_id: str | None = None
    perturbed_experiment_id: str | None = None
    created_at: datetime


class ExperimentInvariantDto(BaseModel):
    """Shared invariant parameters guaranteed equal across paired runs (`BR-005`)."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    route_id: str = Field(min_length=1)
    network_version: str = Field(min_length=1)
    demand_profile: DemandProfile
    departure_time_s: int = Field(ge=0)
    random_seed: int = Field(ge=0)


class ObservedStopComparisonRowDto(BaseModel):
    """Single stop-by-stop observed arrival comparison row (`BR-006`)."""

    model_config = ConfigDict(extra="forbid")

    stop_id: str = Field(min_length=1)
    stop_name: str = Field(min_length=1)
    sequence_index: int = Field(ge=0)
    baseline_observed_arrival_s: int | None = Field(default=None, ge=0)
    perturbed_observed_arrival_s: int | None = Field(default=None, ge=0)
    observed_difference_s: int | None = None
    baseline_dwell_s: int | None = Field(default=None, ge=0)
    perturbed_dwell_s: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_observed_diff(self) -> ObservedStopComparisonRowDto:
        if (
            self.baseline_observed_arrival_s is not None
            and self.perturbed_observed_arrival_s is not None
        ):
            expected = self.perturbed_observed_arrival_s - self.baseline_observed_arrival_s
            if self.observed_difference_s != expected:
                raise ValueError(
                    f"observed_difference_s ({self.observed_difference_s}) must equal "
                    f"perturbed_observed_arrival_s - baseline_observed_arrival_s ({expected})."
                )
        elif self.observed_difference_s is not None:
            raise ValueError(
                "observed_difference_s must be None when either observed arrival is missing."
            )
        return self


class ExperimentComparisonDto(BaseModel):
    """Paired stop-by-stop observed arrival comparison (`BR-005`, `BR-006`)."""

    model_config = ConfigDict(extra="forbid")

    baseline_experiment_id: str = Field(min_length=1)
    perturbed_experiment_id: str = Field(min_length=1)
    invariants: ExperimentInvariantDto
    invariants_match: bool
    intervention_stop_id: str | None = None
    stop_hold_s: int = Field(default=0, ge=0, le=900)
    traffic_multiplier: float = Field(default=1.0, ge=0.5, le=2.5)
    data_origin: Literal["OBSERVED_SIMULATED_ARRIVALS"] = "OBSERVED_SIMULATED_ARRIVALS"
    rows: list[ObservedStopComparisonRowDto] = Field(default_factory=list)
    max_observed_difference_s: int | None = None
    mean_observed_difference_s: float | None = None
    simulation_domain_notice: str = OBSERVED_COMPARISON_NOTICE


class ExperimentDetailDto(BaseModel):
    """Full experiment job lifecycle, paired run IDs, invariants, and optional comparison."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: ExperimentJobStatus
    route_id: str = Field(min_length=1)
    network_version: str | None = None
    demand_profile: DemandProfile
    departure_time_s: int = Field(ge=0)
    random_seed: int = Field(ge=0)
    intervention_stop_id: str | None = None
    stop_hold_s: int = Field(default=0, ge=0, le=900)
    traffic_multiplier: float = Field(default=1.0, ge=0.5, le=2.5)
    baseline_experiment_id: str | None = None
    perturbed_experiment_id: str | None = None
    baseline_journey_id: str | None = None
    perturbed_journey_id: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    invariants: ExperimentInvariantDto | None = None
    comparison: ExperimentComparisonDto | None = None


class TelemetryEventIn(BaseModel):
    """Client/server wall-clock latency telemetry event (`REQ-016`, `EVAL-004`, `CON-008`)."""

    model_config = ConfigDict(extra="forbid")

    journey_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    sequence_number: int = Field(default=0, ge=0)
    transport_mode: TransportMode
    poll_interval_s: int | None = Field(default=None, ge=1)
    worker_generated_at_ms: int | None = Field(default=None, ge=0)
    prediction_completed_at_ms: int | None = Field(default=None, ge=0)
    server_published_at_ms: int | None = Field(default=None, ge=0)
    client_request_start_ms: int | None = Field(default=None, ge=0)
    client_received_at_ms: int | None = Field(default=None, ge=0)
    client_rendered_at_ms: int | None = Field(default=None, ge=0)
    clock_offset_ms: int = 0
    status: TelemetryStatus = "OK"
    error_code: str | None = Field(default=None, max_length=64)

    @field_validator(
        "sequence_number",
        "poll_interval_s",
        "worker_generated_at_ms",
        "prediction_completed_at_ms",
        "server_published_at_ms",
        "client_request_start_ms",
        "client_received_at_ms",
        "client_rendered_at_ms",
        "clock_offset_ms",
        mode="before",
    )
    @classmethod
    def _strict_ints(cls, value: Any) -> Any:
        return _require_strict_int(value)


class TelemetryGroupSummaryDto(BaseModel):
    """Aggregated decomposed latency & failure summary per `(transport_mode, poll_interval_s)`."""

    model_config = ConfigDict(extra="forbid")

    transport_mode: TransportMode
    poll_interval_s: int | None = None
    total_events: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    discarded_stale_count: int = Field(ge=0)
    failure_rate: float = Field(ge=0.0, le=1.0)
    server_to_display_median_ms: float | None = None
    server_to_display_p95_ms: float | None = None
    request_to_display_median_ms: float | None = None
    request_to_display_p95_ms: float | None = None
    end_to_end_freshness_median_ms: float | None = None
    end_to_end_freshness_p95_ms: float | None = None
    meets_demo_2s_target: bool | None = None


class TelemetrySummaryDto(BaseModel):
    """Aggregate synchronization latency report returned by `GET /telemetry/summary`."""

    model_config = ConfigDict(extra="forbid")

    total_events: int = Field(ge=0)
    groups: list[TelemetryGroupSummaryDto] = Field(default_factory=list)
    simulation_domain_notice: str = SIMULATION_DOMAIN_NOTICE


class ErrorEnvelope(BaseModel):
    """Canonical structured JSON error body for all `4xx`/`5xx` API responses."""

    model_config = ConfigDict(extra="forbid")

    error_code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
