import json
import time
from abc import ABC, abstractmethod
from typing import Iterator, Optional
from .schema import CanonicalVehicleState

class BaseSourceAdapter(ABC):
    def __init__(self):
        self.status = "playing"  # "playing" | "paused" | "finished"

    def play(self):
        if self.status != "finished":
            self.status = "playing"

    def pause(self):
        if self.status == "playing":
            self.status = "paused"

    @abstractmethod
    def stream(self) -> Iterator[CanonicalVehicleState]:
        pass


class LiveSumoAdapter(BaseSourceAdapter):
    """Transforms live TraCI events in JSON schemes"""
    def __init__(self, traci_conn, net, run_id: str, run_metadata: dict, snapshot_interval: int = 5):
        super().__init__()
        self.traci = traci_conn
        self.net = net
        self.run_id = run_id
        self.run_metadata = run_metadata
        self.snapshot_interval = snapshot_interval
        self.dwell_tracker = {}

    def stream(self) -> Iterator[CanonicalVehicleState]:
        step = 0
        while self.traci.simulation.getMinExpectedNumber() > 0:
            if self.status == "paused":
                time.sleep(0.1)
                continue

            self.traci.simulationStep()
            sim_time = self.traci.simulation.getTime()

            for veh_id in self.traci.vehicle.getIDList():
                if not veh_id.startswith("1010-"):
                    continue

                stop_state = self.traci.vehicle.getStopState(veh_id)
                is_stopped = (stop_state & 1) == 1
                self.dwell_tracker[veh_id] = (self.dwell_tracker.get(veh_id, 0) + 1) if is_stopped else 0

                if step % self.snapshot_interval == 0:
                    x, y = self.traci.vehicle.getPosition(veh_id)
                    lon, lat = self.net.convertXY2LonLat(x, y)
                    next_stops = [s[2] for s in self.traci.vehicle.getNextStops(veh_id)]
                    curr_stop = next_stops[0] if (is_stopped and next_stops) else None

                    yield CanonicalVehicleState(
                        source_type="live_sumo",
                        playback_status=self.status,
                        run_id=self.run_id,
                        journey_id=veh_id,
                        route_id="1010",
                        sim_time_sec=sim_time,
                        road_id=self.traci.vehicle.getRoadID(veh_id),
                        lane_pos_m=round(self.traci.vehicle.getLanePosition(veh_id), 2),
                        speed_mps=round(self.traci.vehicle.getSpeed(veh_id), 2),
                        dist_traveled_m=round(self.traci.vehicle.getDistance(veh_id), 2),
                        lat=round(lat, 6),
                        lon=round(lon, 6),
                        current_stop_id=curr_stop,
                        current_dwell_sec=self.dwell_tracker[veh_id],
                        next_stop_id=next_stops[0] if next_stops else None,
                        remaining_stops=next_stops,
                        metadata=self.run_metadata,
                    )
            step += 1

        self.status = "finished"


class RecordedReplayAdapter(BaseSourceAdapter):
    """Reproduces a saved NDJSON file with play/pause and simulation clock."""
    def __init__(self, ndjson_path: str, speed_factor: float = 1.0):
        super().__init__()
        self.ndjson_path = ndjson_path
        self.speed_factor = speed_factor  # 1.0 = tempo real; 10.0 = 10x mais rápido; 0 = sem espera

    def stream(self) -> Iterator[CanonicalVehicleState]:
        last_sim_time: Optional[float] = None
        with open(self.ndjson_path, "r", encoding="utf-8") as f:
            for line in f:
                while self.status == "paused":
                    time.sleep(0.1)

                raw = json.loads(line)
                sim_time = raw["sim_time_sec"]

                if last_sim_time is not None and self.speed_factor > 0:
                    delta = max(0.0, sim_time - last_sim_time)
                    time.sleep(delta / self.speed_factor)
                last_sim_time = sim_time

                raw["source_type"] = "recorded_replay"
                raw["playback_status"] = self.status
                yield CanonicalVehicleState(**raw)

        self.status = "finished"