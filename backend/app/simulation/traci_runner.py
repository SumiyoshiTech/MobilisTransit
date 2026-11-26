"""Deterministic Eclipse SUMO / TraCI simulation worker for Mobilis Line 9.

Supports both the fast 4-stop synthetic fixture (`sumo/fixtures/sample_line9/`) and
the canonical 22-stop circular Line 9 network (`sumo/`, where `gtfs_lra_0096` repeats
at `P01` and `P22`). Scales background traffic via run-scoped temporary XML files
rather than SUMO's global `--scale` flag so Line 9 buses are never duplicated or dropped.
"""

from __future__ import annotations

import logging
import math
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import sumolib
import traci
import traci.constants as tc
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from backend.app.simulation.manifest_loader import (
    NetworkManifest,
    StopManifest,
    load_and_validate_manifest,
)
from backend.app.simulation.sumo_env import ensure_sumo_home_env, resolve_sumo_binary

logger = logging.getLogger(__name__)

DemandProfile = Literal["off_peak", "campus_rush", "heavy_congestion"]
VehicleStatus = Literal["IN_TRANSIT", "AT_STOP", "COMPLETED"]

DEMAND_PROFILE_BASE_SCALE: dict[DemandProfile, float] = {
    "off_peak": 0.6,
    "campus_rush": 1.0,
    "heavy_congestion": 1.5,
}

BG_WARMUP_S = 60
DEFAULT_SYNTHETIC_CORRIDOR_VPH = 120.0
MIN_SYNTHETIC_CORRIDOR_EDGES = 3
DEFAULT_SCHEDULED_STOP_DURATION_S = 10.0
_INVALID_SUMO_VALUE_THRESHOLD = -1e8


class SimulationError(RuntimeError):
    """Base error raised during SUMO simulation execution."""


class SimulationValidationError(ValueError):
    """Raised when simulation parameters or manifest references fail validation before SUMO launch."""


class SimulationTimeoutError(SimulationError, TimeoutError):
    """Raised when a SUMO TraCI simulation exceeds its configured wall-clock timeout."""


class SimulationExecutionError(SimulationError):
    """Raised when SUMO finishes abnormally, drops, or duplicates the target bus vehicle."""


class SimulationRunConfig(BaseModel):
    """Validated configuration for a single deterministic SUMO bus journey run."""

    model_config = ConfigDict(extra="forbid")

    network_dir: Path
    journey_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    demand_profile: DemandProfile
    departure_time_s: int = Field(ge=0)
    random_seed: int = Field(ge=0)
    intervention_stop_id: str | None = None
    stop_hold_s: int = Field(default=0, ge=0, le=900)
    traffic_multiplier: float = Field(default=1.0, ge=0.5, le=2.5)
    snapshot_interval_s: int = Field(default=1, ge=1)
    timeout_s: int = Field(default=180, ge=1)

    @field_validator("journey_id", "scenario_id", "execution_id")
    @classmethod
    def _reject_blank_ids(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Identifier must not be blank.")
        return stripped

    @field_validator("intervention_stop_id")
    @classmethod
    def _validate_optional_stop_id(cls, value: str | None) -> str | None:
        if value is not None:
            stripped = value.strip()
            if not stripped:
                raise ValueError("intervention_stop_id must not be blank when provided.")
            return stripped
        return None

    @field_validator(
        "departure_time_s",
        "random_seed",
        "stop_hold_s",
        "snapshot_interval_s",
        "timeout_s",
        mode="before",
    )
    @classmethod
    def _require_strict_int(cls, value: Any) -> Any:
        if isinstance(value, (bool, float)):
            raise PydanticCustomError(
                "strict_int",
                "Field must be an integer, not a boolean or float.",
            )
        return value

    @field_validator("traffic_multiplier", mode="before")
    @classmethod
    def _validate_traffic_multiplier_type(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise PydanticCustomError(
                "strict_float",
                "traffic_multiplier must be a finite number, not a boolean.",
            )
        if isinstance(value, (int, float)) and not math.isfinite(float(value)):
            raise ValueError("traffic_multiplier must be finite.")
        return value

    @model_validator(mode="after")
    def _validate_intervention_consistency(self) -> SimulationRunConfig:
        if self.stop_hold_s > 0 and self.intervention_stop_id is None:
            raise ValueError("intervention_stop_id is required whenever stop_hold_s > 0.")
        return self


class RawVehicleState(BaseModel):
    """Single 1 s-resolution raw telemetry snapshot emitted by the TraCI worker."""

    model_config = ConfigDict(extra="forbid")

    journey_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    sequence_number: int = Field(ge=1)
    simulation_clock_s: int = Field(ge=0)
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


class ObservedStopArrival(BaseModel):
    """Ground-truth observed bus arrival and dwell duration at a single ordered stop."""

    model_config = ConfigDict(extra="forbid")

    stop_id: str = Field(min_length=1)
    sequence_index: int = Field(ge=0)
    sumo_bus_stop_id: str = Field(min_length=1)
    observed_arrival_s: int = Field(ge=0)
    dwell_duration_s: int = Field(ge=0)


class SimulationRunResult(BaseModel):
    """Complete output of one deterministic SUMO simulation run."""

    model_config = ConfigDict(extra="forbid")

    config: SimulationRunConfig
    network_version: str = Field(min_length=1)
    raw_snapshots: list[RawVehicleState]
    observed_arrivals: list[ObservedStopArrival]
    completed_cleanly: bool
    sim_duration_s: int = Field(ge=0)


@dataclass(frozen=True)
class _UpcomingStop:
    stopping_place_id: str
    duration: float
    until: float


@dataclass(frozen=True)
class _PreparedRunInputs:
    net_file: Path
    route_files: tuple[Path, ...]
    additional_files: tuple[Path, ...]
    sim_begin: int
    sim_end: int


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clamp_non_negative(value: float) -> float:
    if not math.isfinite(value) or value <= _INVALID_SUMO_VALUE_THRESHOLD or value < 0.0:
        return 0.0
    return float(value)


def _edge_pair_allows_passenger(edge_from: Any, edge_to: Any) -> bool:
    connections = edge_from.getConnections(edge_to)
    return any(
        conn.getFromLane().allows("passenger") and conn.getToLane().allows("passenger")
        for conn in connections
    )


@lru_cache(maxsize=8)
def _extract_passenger_corridors(
    net_path_str: str,
    route_edges: tuple[str, ...],
) -> tuple[tuple[str, ...], ...]:
    """Partition a bus route into contiguous passenger-allowed edge sub-corridors."""
    net = sumolib.net.readNet(net_path_str)
    corridors: list[tuple[str, ...]] = []
    current: list[str] = []
    prev_edge: Any = None

    for edge_id in route_edges:
        try:
            edge = net.getEdge(edge_id)
        except KeyError:
            if len(current) >= MIN_SYNTHETIC_CORRIDOR_EDGES:
                corridors.append(tuple(current))
            current = []
            prev_edge = None
            continue

        if not edge.allows("passenger"):
            if len(current) >= MIN_SYNTHETIC_CORRIDOR_EDGES:
                corridors.append(tuple(current))
            current = []
            prev_edge = None
            continue

        if current and prev_edge is not None and not _edge_pair_allows_passenger(prev_edge, edge):
            if len(current) >= MIN_SYNTHETIC_CORRIDOR_EDGES:
                corridors.append(tuple(current))
            current = [edge_id]
        else:
            current.append(edge_id)
        prev_edge = edge

    if len(current) >= MIN_SYNTHETIC_CORRIDOR_EDGES:
        corridors.append(tuple(current))

    return tuple(corridors)


def _scale_flow_element(
    flow_elem: ET.Element,
    effective_scale: float,
    sim_begin: int,
    sim_end: int,
) -> None:
    """Scale a background SUMO <flow> element without affecting any bus vehicle."""
    flow_elem.set("begin", str(sim_begin))
    flow_elem.set("end", str(sim_end))

    if "vehsPerHour" in flow_elem.attrib:
        base_vph = float(flow_elem.attrib["vehsPerHour"])
        flow_elem.set("vehsPerHour", f"{max(1.0, base_vph * effective_scale):.2f}")
    elif "probability" in flow_elem.attrib:
        base_prob = float(flow_elem.attrib["probability"])
        scaled_prob = min(0.95, max(0.001, base_prob * effective_scale))
        flow_elem.set("probability", f"{scaled_prob:.6f}")
    elif "period" in flow_elem.attrib:
        base_period = float(flow_elem.attrib["period"])
        flow_elem.set("period", f"{max(0.2, base_period / effective_scale):.4f}")
    elif "number" in flow_elem.attrib:
        base_number = round(float(flow_elem.attrib["number"]))
        flow_elem.set("number", str(max(1, round(base_number * effective_scale))))


def _is_bus_flow(flow_elem: ET.Element, manifest: NetworkManifest) -> bool:
    flow_id = flow_elem.attrib.get("id", "")
    flow_type = flow_elem.attrib.get("type", "")
    if flow_id == manifest.bus_vehicle_id or flow_type == "bus":
        return True
    return bool(manifest.bus_vehicle_prefix and flow_id.startswith(manifest.bus_vehicle_prefix))


def _rewrite_route_or_vehicle_xml(
    source_path: Path,
    dest_path: Path,
    manifest: NetworkManifest,
    departure_time_s: int,
    effective_scale: float,
    sim_begin: int,
    sim_end: int,
    isolate_target_bus: bool,
) -> tuple[bool, bool, tuple[str, ...] | None]:
    """Copy a route/vehicle XML file, updating target bus depart and scaling background flows.

    Returns ``(found_target_bus, scaled_any_bg_flow, bus_route_edges)``.
    """
    tree = ET.parse(source_path)
    root = tree.getroot()

    found_target_bus = False
    scaled_bg_flow = False
    bus_route_edges: tuple[str, ...] | None = None

    for route_elem in root.findall("route"):
        if route_elem.attrib.get("id") == manifest.bus_route_id:
            edges_str = route_elem.attrib.get("edges", "").strip()
            if edges_str:
                bus_route_edges = tuple(edges_str.split())

    for flow_elem in root.findall("flow"):
        if not _is_bus_flow(flow_elem, manifest):
            _scale_flow_element(flow_elem, effective_scale, sim_begin, sim_end)
            scaled_bg_flow = True

    vehicles_to_remove: list[ET.Element] = []
    for veh_elem in root.findall("vehicle"):
        veh_id = veh_elem.attrib.get("id", "")
        if veh_id == manifest.bus_vehicle_id:
            veh_elem.set("depart", str(departure_time_s))
            found_target_bus = True
        elif isolate_target_bus:
            vehicles_to_remove.append(veh_elem)

    for veh_elem in vehicles_to_remove:
        root.remove(veh_elem)

    tree.write(dest_path, encoding="utf-8", xml_declaration=True)
    return found_target_bus, scaled_bg_flow, bus_route_edges


def _write_synthetic_bg_traffic_file(
    dest_path: Path,
    corridors: tuple[tuple[str, ...], ...],
    effective_scale: float,
    sim_begin: int,
    sim_end: int,
) -> None:
    """Write deterministic background passenger flows along passenger-allowed route corridors."""
    root = ET.Element("routes")
    ET.SubElement(
        root,
        "vType",
        {
            "id": "mt_bg_passenger",
            "vClass": "passenger",
            "length": "4.5",
            "maxSpeed": "13.89",
            "accel": "2.6",
            "decel": "4.5",
            "sigma": "0.5",
        },
    )

    scaled_vph = max(1.0, DEFAULT_SYNTHETIC_CORRIDOR_VPH * effective_scale)
    for idx, corridor_edges in enumerate(corridors):
        route_id = f"mt_bg_corridor_{idx}"
        ET.SubElement(root, "route", {"id": route_id, "edges": " ".join(corridor_edges)})
        ET.SubElement(
            root,
            "flow",
            {
                "id": f"mt_bg_flow_{idx}",
                "type": "mt_bg_passenger",
                "route": route_id,
                "begin": str(sim_begin),
                "end": str(sim_end),
                "vehsPerHour": f"{scaled_vph:.2f}",
                "departLane": "best",
                "departSpeed": "max",
            },
        )

    ET.ElementTree(root).write(dest_path, encoding="utf-8", xml_declaration=True)


def _prepare_run_inputs(
    manifest: NetworkManifest,
    config: SimulationRunConfig,
    tmp_dir: Path,
) -> _PreparedRunInputs:
    network_dir = config.network_dir.resolve()
    net_path = (network_dir / manifest.net_file).resolve()
    effective_scale = DEMAND_PROFILE_BASE_SCALE[config.demand_profile] * config.traffic_multiplier

    sim_begin = max(0, config.departure_time_s - BG_WARMUP_S)
    horizon_s = max(3600, len(manifest.stops) * 240 + config.stop_hold_s + 1800)
    sim_end = config.departure_time_s + horizon_s

    route_files: list[Path] = []
    additional_files: list[Path] = []
    found_target_bus = False
    has_bg_flow = False
    bus_route_edges: tuple[str, ...] | None = None

    if manifest.route_file is not None:
        orig_route_path = (network_dir / manifest.route_file).resolve()
        scoped_route_path = tmp_dir / "run_routes.rou.xml"
        found_bus, scaled_flow, edges = _rewrite_route_or_vehicle_xml(
            source_path=orig_route_path,
            dest_path=scoped_route_path,
            manifest=manifest,
            departure_time_s=config.departure_time_s,
            effective_scale=effective_scale,
            sim_begin=sim_begin,
            sim_end=sim_end,
            isolate_target_bus=True,
        )
        found_target_bus = found_target_bus or found_bus
        has_bg_flow = has_bg_flow or scaled_flow
        if edges is not None:
            bus_route_edges = edges
        route_files.append(scoped_route_path)

    for idx, rel_add in enumerate(manifest.additional_files):
        orig_add_path = (network_dir / rel_add).resolve()
        # Only rewrite additional files that declare routes, vehicles, or flows.
        root = ET.parse(orig_add_path).getroot()
        contains_vehicle_or_flow = (
            root.find("vehicle") is not None or root.find("flow") is not None
        )
        if contains_vehicle_or_flow:
            scoped_add_path = tmp_dir / f"run_add_{idx}_{orig_add_path.name}"
            found_bus, scaled_flow, edges = _rewrite_route_or_vehicle_xml(
                source_path=orig_add_path,
                dest_path=scoped_add_path,
                manifest=manifest,
                departure_time_s=config.departure_time_s,
                effective_scale=effective_scale,
                sim_begin=sim_begin,
                sim_end=sim_end,
                isolate_target_bus=True,
            )
            found_target_bus = found_target_bus or found_bus
            has_bg_flow = has_bg_flow or scaled_flow
            if edges is not None:
                bus_route_edges = edges
            additional_files.append(scoped_add_path)
        else:
            additional_files.append(orig_add_path)

    if not found_target_bus:
        raise SimulationValidationError(
            f"Target bus vehicle {manifest.bus_vehicle_id!r} was not found in "
            f"route_file or additional_files of {network_dir}."
        )

    if not has_bg_flow and bus_route_edges is not None:
        corridors = _extract_passenger_corridors(str(net_path), bus_route_edges)
        if corridors:
            bg_route_path = tmp_dir / "run_bg_traffic.rou.xml"
            _write_synthetic_bg_traffic_file(
                dest_path=bg_route_path,
                corridors=corridors,
                effective_scale=effective_scale,
                sim_begin=sim_begin,
                sim_end=sim_end,
            )
            route_files.append(bg_route_path)

    return _PreparedRunInputs(
        net_file=net_path,
        route_files=tuple(route_files),
        additional_files=tuple(additional_files),
        sim_begin=sim_begin,
        sim_end=sim_end,
    )


def _get_upcoming_stops(conn: Any, bus_id: str) -> list[_UpcomingStop]:
    """Read upcoming scheduled stops cleanly via `getStops` with `getNextStops` fallback."""
    if hasattr(conn.vehicle, "getStops"):
        return [
            _UpcomingStop(
                stopping_place_id=str(stop.stoppingPlaceID),
                duration=float(stop.duration),
                until=float(stop.until),
            )
            for stop in conn.vehicle.getStops(bus_id)
        ]
    return [
        _UpcomingStop(
            stopping_place_id=str(item[2]),
            duration=float(item[4]),
            until=float(item[5]),
        )
        for item in conn.vehicle.getNextStops(bus_id)
    ]


def _apply_stop_hold_intervention(
    conn: Any,
    bus_id: str,
    target_stop: StopManifest,
    target_seq_index: int,
    next_stop_cursor: int,
    stop_hold_s: int,
) -> None:
    """Extend scheduled stop duration at `target_seq_index` by `stop_hold_s` seconds."""
    upcoming = _get_upcoming_stops(conn, bus_id)
    relative_idx = target_seq_index - next_stop_cursor

    if (
        0 <= relative_idx < len(upcoming)
        and upcoming[relative_idx].stopping_place_id == target_stop.sumo_bus_stop_id
        and hasattr(conn.vehicle, "setStopParameter")
    ):
        scheduled = upcoming[relative_idx]
        base_duration = (
            scheduled.duration
            if scheduled.duration > _INVALID_SUMO_VALUE_THRESHOLD and scheduled.duration >= 0.0
            else DEFAULT_SCHEDULED_STOP_DURATION_S
        )
        new_duration = base_duration + float(stop_hold_s)
        conn.vehicle.setStopParameter(bus_id, relative_idx, "duration", f"{new_duration:.1f}")
        # If the GTFS stop carries a timetable `until`, shift it by `stop_hold_s` so early
        # arrivals still experience the requested additional hold duration.
        if scheduled.until > _INVALID_SUMO_VALUE_THRESHOLD and scheduled.until >= 0.0:
            new_until = scheduled.until + float(stop_hold_s)
            conn.vehicle.setStopParameter(bus_id, relative_idx, "until", f"{new_until:.1f}")
        return

    # Fallback: add/update stop via traci.vehicle.setStop on the mapped busStop ID.
    conn.vehicle.setStop(
        bus_id,
        target_stop.sumo_bus_stop_id,
        duration=DEFAULT_SCHEDULED_STOP_DURATION_S + float(stop_hold_s),
        flags=tc.STOP_BUS_STOP,
    )


def _find_matching_bus_ids(active_ids: set[str], manifest: NetworkManifest) -> list[str]:
    bus_id = manifest.bus_vehicle_id
    return [
        vid
        for vid in active_ids
        if vid == bus_id or vid.startswith(f"{bus_id}.")
    ]


def run_sumo_simulation(
    config: SimulationRunConfig,
    on_snapshot: Callable[[RawVehicleState], None] | None = None,
) -> SimulationRunResult:
    """Run one deterministic headless SUMO simulation via TraCI and collect causal telemetry."""
    # Step 1: Validate manifest and intervention stop strictly BEFORE touching SUMO (BR-007).
    manifest = load_and_validate_manifest(config.network_dir)
    stops = manifest.stops
    stop_id_to_index = {stop.stop_id: stop.sequence_index for stop in stops}

    target_stop_index: int | None = None
    if config.intervention_stop_id is not None:
        if config.intervention_stop_id not in stop_id_to_index:
            raise SimulationValidationError(
                f"Unknown intervention_stop_id {config.intervention_stop_id!r} for route "
                f"{manifest.route_id!r}; valid stops: {[s.stop_id for s in stops]}."
            )
        target_stop_index = stop_id_to_index[config.intervention_stop_id]

    ensure_sumo_home_env()
    sumo_binary = resolve_sumo_binary(gui=False)

    raw_snapshots: list[RawVehicleState] = []
    observed_arrivals: list[ObservedStopArrival] = []

    with tempfile.TemporaryDirectory(prefix="mobilistransit_sumo_") as tmp_dir_str:
        prepared = _prepare_run_inputs(manifest, config, Path(tmp_dir_str))
        cmd: list[str] = [
            str(sumo_binary),
            "--net-file",
            str(prepared.net_file),
            "--begin",
            str(prepared.sim_begin),
            "--end",
            str(prepared.sim_end),
            "--step-length",
            "1.0",
            "--seed",
            str(config.random_seed),
            "--no-step-log",
            "true",
            "--no-warnings",
            "true",
            "--duration-log.disable",
            "true",
        ]
        if prepared.route_files:
            cmd.extend(["--route-files", ",".join(str(p) for p in prepared.route_files)])
        if prepared.additional_files:
            cmd.extend(["--additional-files", ",".join(str(p) for p in prepared.additional_files)])

        label = f"mt_{uuid4().hex[:12]}"
        bus_id = manifest.bus_vehicle_id
        started = False

        # Strictly causal bus tracking state
        bus_seen = False
        bus_completed = False
        hold_applied = False
        next_stop_cursor = 0
        in_stop = False
        active_stop_index: int | None = None
        active_stop_arrival_s: int | None = None
        ongoing_stop_elapsed_s = 0
        last_completed_stop_dwell_s = 0
        last_visited_stop_id: str | None = None
        sequence_number = 0
        active_tick_count = 0
        last_bus_clock_s = config.departure_time_s
        last_edge_id = ""
        last_lane_pos = 0.0
        last_distance = 0.0

        wall_start = time.monotonic()

        try:
            traci.start(cmd, label=label)
            started = True
            conn = traci.getConnection(label)

            while conn.simulation.getMinExpectedNumber() > 0:
                if time.monotonic() - wall_start > config.timeout_s:
                    raise SimulationTimeoutError(
                        f"SUMO simulation exceeded timeout of {config.timeout_s}s "
                        f"(journey_id={config.journey_id}, execution_id={config.execution_id})."
                    )

                conn.simulationStep()
                sim_clock_s = round(float(conn.simulation.getTime()))
                if sim_clock_s > prepared.sim_end:
                    break

                active_ids = set(conn.vehicle.getIDList())
                matching_buses = _find_matching_bus_ids(active_ids, manifest)
                if len(matching_buses) > 1:
                    raise SimulationExecutionError(
                        f"Duplicate target bus vehicles detected in SUMO: {sorted(matching_buses)}"
                    )

                if bus_id in active_ids:
                    bus_seen = True
                    last_bus_clock_s = sim_clock_s

                    if (
                        config.stop_hold_s > 0
                        and target_stop_index is not None
                        and not hold_applied
                    ):
                        _apply_stop_hold_intervention(
                            conn=conn,
                            bus_id=bus_id,
                            target_stop=stops[target_stop_index],
                            target_seq_index=target_stop_index,
                            next_stop_cursor=next_stop_cursor,
                            stop_hold_s=config.stop_hold_s,
                        )
                        hold_applied = True

                    edge_id = str(conn.vehicle.getRoadID(bus_id))
                    lane_pos = _clamp_non_negative(float(conn.vehicle.getLanePosition(bus_id)))
                    distance_m = _clamp_non_negative(float(conn.vehicle.getDistance(bus_id)))
                    speed_mps = _clamp_non_negative(float(conn.vehicle.getSpeed(bus_id)))
                    stop_state = int(conn.vehicle.getStopState(bus_id))
                    upcoming_stops = _get_upcoming_stops(conn, bus_id)

                    last_edge_id = edge_id
                    last_lane_pos = lane_pos
                    last_distance = distance_m

                    is_stopped = bool(stop_state & 1)

                    if not in_stop and is_stopped and next_stop_cursor < len(stops):
                        # Match by ordered sequence cursor (handles circular loop terminal reuse).
                        matched_index = next_stop_cursor
                        if upcoming_stops:
                            head_stop_id = upcoming_stops[0].stopping_place_id
                            if head_stop_id != stops[matched_index].sumo_bus_stop_id:
                                for candidate in range(next_stop_cursor, len(stops)):
                                    if stops[candidate].sumo_bus_stop_id == head_stop_id:
                                        matched_index = candidate
                                        break
                        in_stop = True
                        active_stop_index = matched_index
                        active_stop_arrival_s = sim_clock_s
                        ongoing_stop_elapsed_s = 0
                    elif in_stop and is_stopped and active_stop_arrival_s is not None:
                        ongoing_stop_elapsed_s = max(0, sim_clock_s - active_stop_arrival_s)
                    elif (
                        in_stop
                        and not is_stopped
                        and active_stop_index is not None
                        and active_stop_arrival_s is not None
                    ):
                        completed_stop = stops[active_stop_index]
                        dwell_s = max(0, sim_clock_s - active_stop_arrival_s)
                        observed_arrivals.append(
                            ObservedStopArrival(
                                stop_id=completed_stop.stop_id,
                                sequence_index=completed_stop.sequence_index,
                                sumo_bus_stop_id=completed_stop.sumo_bus_stop_id,
                                observed_arrival_s=active_stop_arrival_s,
                                dwell_duration_s=dwell_s,
                            )
                        )
                        last_completed_stop_dwell_s = dwell_s
                        last_visited_stop_id = completed_stop.stop_id
                        next_stop_cursor = active_stop_index + 1
                        in_stop = False
                        active_stop_index = None
                        active_stop_arrival_s = None
                        ongoing_stop_elapsed_s = 0

                    if in_stop and active_stop_index is not None:
                        status: VehicleStatus = "AT_STOP"
                        current_segment_index = active_stop_index
                        snapshot_last_visited = stops[active_stop_index].stop_id
                        snapshot_next_stop = (
                            stops[active_stop_index + 1].stop_id
                            if active_stop_index + 1 < len(stops)
                            else None
                        )
                    else:
                        status = "IN_TRANSIT"
                        current_segment_index = min(next_stop_cursor, len(stops) - 1)
                        snapshot_last_visited = last_visited_stop_id
                        snapshot_next_stop = (
                            stops[next_stop_cursor].stop_id
                            if next_stop_cursor < len(stops)
                            else None
                        )

                    should_emit = (active_tick_count % config.snapshot_interval_s) == 0
                    active_tick_count += 1
                    if should_emit:
                        sequence_number += 1
                        snapshot = RawVehicleState(
                            journey_id=config.journey_id,
                            route_id=manifest.route_id,
                            scenario_id=config.scenario_id,
                            execution_id=config.execution_id,
                            sequence_number=sequence_number,
                            simulation_clock_s=sim_clock_s,
                            worker_generated_at_ms=_now_ms(),
                            vehicle_id=bus_id,
                            status=status,
                            edge_id=edge_id,
                            lane_position_m=lane_pos,
                            distance_travelled_m=distance_m,
                            speed_mps=speed_mps,
                            current_segment_index=current_segment_index,
                            last_visited_stop_id=snapshot_last_visited,
                            next_stop_id=snapshot_next_stop,
                            ongoing_stop_elapsed_s=ongoing_stop_elapsed_s,
                            last_completed_stop_dwell_s=last_completed_stop_dwell_s,
                            demand_profile=config.demand_profile,
                        )
                        raw_snapshots.append(snapshot)
                        if on_snapshot is not None:
                            on_snapshot(snapshot)

                elif bus_seen:
                    # Target bus exited the network; finalize any active terminal stop and break.
                    if (
                        in_stop
                        and active_stop_index is not None
                        and active_stop_arrival_s is not None
                    ):
                        completed_stop = stops[active_stop_index]
                        dwell_s = max(0, sim_clock_s - active_stop_arrival_s)
                        observed_arrivals.append(
                            ObservedStopArrival(
                                stop_id=completed_stop.stop_id,
                                sequence_index=completed_stop.sequence_index,
                                sumo_bus_stop_id=completed_stop.sumo_bus_stop_id,
                                observed_arrival_s=active_stop_arrival_s,
                                dwell_duration_s=dwell_s,
                            )
                        )
                        last_completed_stop_dwell_s = dwell_s
                        last_visited_stop_id = completed_stop.stop_id
                        next_stop_cursor = active_stop_index + 1
                        in_stop = False
                        active_stop_index = None
                        active_stop_arrival_s = None
                        ongoing_stop_elapsed_s = 0

                    bus_completed = True
                    last_bus_clock_s = sim_clock_s
                    sequence_number += 1
                    final_snapshot = RawVehicleState(
                        journey_id=config.journey_id,
                        route_id=manifest.route_id,
                        scenario_id=config.scenario_id,
                        execution_id=config.execution_id,
                        sequence_number=sequence_number,
                        simulation_clock_s=sim_clock_s,
                        worker_generated_at_ms=_now_ms(),
                        vehicle_id=bus_id,
                        status="COMPLETED",
                        edge_id=last_edge_id,
                        lane_position_m=last_lane_pos,
                        distance_travelled_m=last_distance,
                        speed_mps=0.0,
                        current_segment_index=len(stops) - 1,
                        last_visited_stop_id=last_visited_stop_id,
                        next_stop_id=None,
                        ongoing_stop_elapsed_s=0,
                        last_completed_stop_dwell_s=last_completed_stop_dwell_s,
                        demand_profile=config.demand_profile,
                    )
                    raw_snapshots.append(final_snapshot)
                    if on_snapshot is not None:
                        on_snapshot(final_snapshot)
                    break

        finally:
            if started:
                try:
                    traci.switch(label)
                    traci.close(wait=True)
                except Exception:
                    logger.debug(
                        "Suppressed TraCI teardown exception for label=%s.",
                        label,
                        exc_info=True,
                    )

    if not bus_seen:
        raise SimulationExecutionError(
            f"Target bus vehicle {bus_id!r} never appeared in SUMO simulation "
            f"(network={manifest.network_version}, depart={config.departure_time_s})."
        )

    sim_duration_s = max(0, last_bus_clock_s - config.departure_time_s)
    completed_cleanly = bus_seen and bus_completed and len(observed_arrivals) == len(stops)

    return SimulationRunResult(
        config=config,
        network_version=manifest.network_version,
        raw_snapshots=raw_snapshots,
        observed_arrivals=observed_arrivals,
        completed_cleanly=completed_cleanly,
        sim_duration_s=sim_duration_s,
    )
