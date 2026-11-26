"""Unified SourceAdapter transforming SUMO and replay telemetry into one canonical schema.

Satisfies REQ-002 so the prediction service, REST/SSE API, and mobile/admin clients
operate identically across live SUMO runs and recorded journey replays without
source-specific branches.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from backend.app.simulation.traci_runner import (
    DemandProfile,
    RawVehicleState,
    VehicleStatus,
)

SourceType = Literal["SIMULATION", "RECORDED_REPLAY"]
PlaybackState = Literal["PLAYING", "PAUSED", "STOPPED"]


class CanonicalVehicleState(BaseModel):
    """Canonical normalized vehicle telemetry snapshot shared across all data sources."""

    model_config = ConfigDict(extra="forbid")

    source: SourceType
    journey_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    sequence_number: int = Field(ge=1)
    simulation_clock_s: int = Field(ge=0)
    playback_state: PlaybackState
    worker_generated_at_ms: int = Field(ge=0)
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
    demand_profile: DemandProfile


@runtime_checkable
class SourceAdapter(Protocol):
    """Protocol converting source-specific vehicle records into `CanonicalVehicleState`.

    Future extension stub (REQ-002; live ingestion explicitly out of scope per REQ-018):
    A future ``GpsTelemetrySourceAdapter`` implementing this protocol would map-match
    live Mobilis GPS telemetry feeds onto Line 9 route segments and emit identical
    ``CanonicalVehicleState`` payloads without altering downstream consumers.
    """

    source_type: SourceType

    def adapt(
        self,
        raw_state: Any,
        *,
        playback_state: PlaybackState = "PLAYING",
    ) -> CanonicalVehicleState:
        """Transform a single source record into `CanonicalVehicleState`."""
        ...


class SumoSourceAdapter:
    """Adapter converting live SUMO `RawVehicleState` records into `CanonicalVehicleState`."""

    source_type: SourceType = "SIMULATION"

    def adapt(
        self,
        raw_state: RawVehicleState,
        *,
        playback_state: PlaybackState = "PLAYING",
    ) -> CanonicalVehicleState:
        if not isinstance(raw_state, RawVehicleState):
            raise TypeError(
                f"SumoSourceAdapter expects RawVehicleState, got {type(raw_state).__name__}."
            )
        effective_playback: PlaybackState = (
            "STOPPED" if raw_state.status == "COMPLETED" else playback_state
        )
        return CanonicalVehicleState(
            source="SIMULATION",
            journey_id=raw_state.journey_id,
            route_id=raw_state.route_id,
            scenario_id=raw_state.scenario_id,
            execution_id=raw_state.execution_id,
            sequence_number=raw_state.sequence_number,
            simulation_clock_s=raw_state.simulation_clock_s,
            playback_state=effective_playback,
            worker_generated_at_ms=raw_state.worker_generated_at_ms,
            vehicle_id=raw_state.vehicle_id,
            status=raw_state.status,
            edge_id=raw_state.edge_id,
            lane_position_m=raw_state.lane_position_m,
            distance_travelled_m=raw_state.distance_travelled_m,
            speed_mps=raw_state.speed_mps,
            current_segment_index=raw_state.current_segment_index,
            last_visited_stop_id=raw_state.last_visited_stop_id,
            next_stop_id=raw_state.next_stop_id,
            ongoing_stop_elapsed_s=raw_state.ongoing_stop_elapsed_s,
            last_completed_stop_dwell_s=raw_state.last_completed_stop_dwell_s,
            demand_profile=raw_state.demand_profile,
        )

    def adapt_many(
        self,
        raw_states: Sequence[RawVehicleState],
        *,
        playback_state: PlaybackState = "PLAYING",
    ) -> list[CanonicalVehicleState]:
        return [self.adapt(item, playback_state=playback_state) for item in raw_states]


class ReplaySourceAdapter:
    """Adapter normalizing recorded snapshots into `CanonicalVehicleState` with replay provenance."""

    source_type: SourceType = "RECORDED_REPLAY"

    def adapt(
        self,
        raw_state: CanonicalVehicleState | RawVehicleState | Mapping[str, Any],
        *,
        playback_state: PlaybackState = "PLAYING",
        execution_id: str | None = None,
        sequence_number: int | None = None,
        worker_generated_at_ms: int | None = None,
    ) -> CanonicalVehicleState:
        if isinstance(raw_state, (CanonicalVehicleState, RawVehicleState)):
            payload = raw_state.model_dump()
        elif isinstance(raw_state, Mapping):
            payload = dict(raw_state)
        else:
            raise TypeError(
                "ReplaySourceAdapter expects CanonicalVehicleState, RawVehicleState, "
                f"or Mapping, got {type(raw_state).__name__}."
            )

        payload["source"] = "RECORDED_REPLAY"
        payload["playback_state"] = playback_state
        if execution_id is not None:
            payload["execution_id"] = execution_id
        if sequence_number is not None:
            payload["sequence_number"] = sequence_number
        if worker_generated_at_ms is not None:
            payload["worker_generated_at_ms"] = worker_generated_at_ms
        elif "worker_generated_at_ms" not in payload:
            payload["worker_generated_at_ms"] = int(time.time() * 1000)

        return CanonicalVehicleState.model_validate(payload)

    def adapt_many(
        self,
        raw_states: Sequence[CanonicalVehicleState | RawVehicleState | Mapping[str, Any]],
        *,
        playback_state: PlaybackState = "PLAYING",
        execution_id: str | None = None,
    ) -> list[CanonicalVehicleState]:
        return [
            self.adapt(
                item,
                playback_state=playback_state,
                execution_id=execution_id,
                sequence_number=idx + 1,
            )
            for idx, item in enumerate(raw_states)
        ]
