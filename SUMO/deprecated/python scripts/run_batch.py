import os
from worker.sim_worker import run_experiment

# Make sure your SUMO asset paths match your actual folder structure
BASE_CONFIG = {
    "net_file": "leiria.net.xml.gz",
    "vtypes_file": "pt_vtypes.xml",
    "stops_file": "gtfs_pt_stops.add.xml",
    "l9_vehicles_file": "gtfs_pt_vehicles_L9.add.xml",
}

SCENARIOS = [
    {
        **BASE_CONFIG,
        "run_id": "L9_BASELINE_01",
        "demand_profile": "offpeak",
        "background_traffic_file": None,  # Or "traffic_offpeak.rou.xml" if generated
        "traffic_multiplier": 1.0,
        "seed": 42,
        "departure_offset_sec": 0,
        "disturbance": None,
    },
    {
        **BASE_CONFIG,
        "run_id": "L9_HOLD_180S_02",
        "demand_profile": "offpeak",
        "background_traffic_file": None,
        "traffic_multiplier": 1.0,
        "seed": 42,
        "departure_offset_sec": 0,
        "disturbance": {"stop_index": 5, "hold_sec": 180},
    },
]

if __name__ == "__main__":
    # Ensure adapter/ and worker/ can be imported as packages
    open("adapter/__init__.py", "a").close()
    open("worker/__init__.py", "a").close()

    for scenario in SCENARIOS:
        run_experiment(scenario, output_dir="recorded_runs")