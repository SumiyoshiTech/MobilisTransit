from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

@dataclass
class CanonicalVehicleState:
    source_type: str                 # "live_sumo" | "recorded_replay" | "gps_live"
    playback_status: str             # "playing" | "paused" | "finished"
    run_id: str
    journey_id: str                  # Ex: "1010-1-001-XJA1-U-07h10-AvBernardoPimen.0"
    route_id: str                    # "1010" (Linha 9)
    sim_time_sec: float              # Relógio da fonte/simulação
    road_id: str                     # Edge SUMO ou segmento mapeado
    lane_pos_m: float
    speed_mps: float
    dist_traveled_m: float
    lat: Optional[float]             # WGS84 coordinates (para Android AR / mapa)
    lon: Optional[float]
    current_stop_id: Optional[str]
    current_dwell_sec: int
    next_stop_id: Optional[str]
    remaining_stops: List[str]
    metadata: Dict[str, Any]         # seed, demand_profile, traffic_multiplier, disturbance

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)