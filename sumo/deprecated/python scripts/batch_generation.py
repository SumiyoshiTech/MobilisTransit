import os
import sys
import json
import random
import logging
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
import xml.etree.ElementTree as ET

# Ensure SUMO tools are available
if "SUMO_HOME" not in os.environ:
    sys.exit("Error: SUMO_HOME environment variable is not set.")

SUMO_TOOLS = os.path.join(os.environ["SUMO_HOME"], "tools")
sys.path.append(SUMO_TOOLS)

# Adiciona a pasta pai (SUMO\) ao path do Python para encontrar 'worker' e 'adapter'
PARENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from worker.sim_worker import run_experiment


# Configure file logging for unattended background execution
logging.basicConfig(
    filename="batch_generation.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

ASSETS = {
    "net_file": os.path.join(PARENT_DIR, "leiria.net.xml.gz"),
    "vtypes_file": os.path.join(PARENT_DIR, "pt_vtypes.xml"),
    "stops_file": os.path.join(PARENT_DIR, "gtfs_pt_stops.add.xml"),
    "l9_vehicles_file": os.path.join(PARENT_DIR, "gtfs_pt_vehicles_L9.add.xml"),
}

OUTPUT_DIR = "recorded_runs"
TRAFFIC_DIR = "generated_traffic"
#Change here the desired number of travels
TARGET_TRAVELS = 300


def count_journeys_in_xml(veh_xml_path: str) -> int:
    """Counts how many Line 9 journeys exist in a single simulated day."""
    tree = ET.parse(veh_xml_path)
    return sum(
        1 for v in tree.getroot().findall("vehicle")
        if v.attrib.get("id", "").startswith("1010-")
    )


def ensure_background_traffic():
    """Generates reusable background traffic profiles if not already created."""
    os.makedirs(TRAFFIC_DIR, exist_ok=True)
    profiles = {
        "weekend_light": {"period": "4.5", "seed": 11},
        "weekday_offpeak": {"period": "2.5", "seed": 22},
        "weekday_campus_rush": {"period": "0.9", "seed": 33},
        "heavy_congestion": {"period": "0.65", "seed": 44},
    }

    generated = {}
    random_trips_script = os.path.join(SUMO_TOOLS, "randomTrips.py")

    for name, cfg in profiles.items():
        out_file = os.path.join(TRAFFIC_DIR, f"traffic_{name}.rou.xml")
        if not os.path.exists(out_file):
            logging.info(f"Generating background traffic profile: {name}...")
            cmd = [
                sys.executable,
                random_trips_script,
                "-n", ASSETS["net_file"],
                "-b", "25200",
                "-e", "72000",
                "-p", cfg["period"],
                "--seed", str(cfg["seed"]),
                "--trip-attributes", 'departLane="best" departSpeed="max"',
                "-o", out_file,
                "--validate",
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        generated[name] = out_file

    return generated


def build_scenario_matrix(traffic_files: dict, journeys_per_day: int) -> list:
    """Builds enough varied day-runs to reach at least TARGET_TRAVELS journeys."""
    num_runs_needed = max(1, (TARGET_TRAVELS // journeys_per_day) + 1)

    day_templates = [
        {"day_type": "weekday_normal", "profile": "weekday_offpeak", "mult_range": (0.9, 1.15)},
        {"day_type": "weekday_peak", "profile": "weekday_campus_rush", "mult_range": (1.0, 1.35)},
        {"day_type": "rainy_congested", "profile": "heavy_congestion", "mult_range": (1.1, 1.4)},
        {"day_type": "weekend_service", "profile": "weekend_light", "mult_range": (0.7, 0.95)},
    ]

    rng = random.Random(2026)
    scenarios = []

    for idx in range(1, num_runs_needed + 1):
        template = day_templates[(idx - 1) % len(day_templates)]
        seed = 1000 + (idx * 17)
        multiplier = round(rng.uniform(*template["mult_range"]), 2)
        dep_offset = rng.choice([-60, 0, 0, 60, 120, 180])

        # Inject a stop-hold disturbance in ~65% of runs
        if rng.random() < 0.65:
            disturbance = {
                "stop_index": rng.randint(2, 15),
                "hold_sec": rng.choice([60, 120, 180, 240, 300]),
            }
        else:
            disturbance = None

        run_id = f"L9_{template['day_type'].upper()}_RUN{idx:03d}_S{seed}"
        scenarios.append({
            **ASSETS,
            "run_id": run_id,
            "demand_profile": f"{template['day_type']}:{template['profile']}",
            "background_traffic_file": traffic_files[template["profile"]],
            "traffic_multiplier": multiplier,
            "seed": seed,
            "departure_offset_sec": dep_offset,
            "disturbance": disturbance,
            "calibration_notes": f"Batch run {idx} ({template['day_type']})",
        })

    return scenarios


def execute_worker(scenario: dict) -> str:
    """Runs a single scenario inside an isolated worker process."""
    run_id = scenario["run_id"]
    snap_path = os.path.join(OUTPUT_DIR, f"{run_id}_snapshots.ndjson")

    # Skip if already generated (allows safe resume if interrupted)
    if os.path.exists(snap_path) and os.path.getsize(snap_path) > 0:
        return f"SKIPPED (already exists): {run_id}"

    run_experiment(scenario, output_dir=OUTPUT_DIR)
    return f"COMPLETED: {run_id}"


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    journeys_per_day = count_journeys_in_xml(ASSETS["l9_vehicles_file"])
    logging.info(f"Detected {journeys_per_day} Line 9 journeys per simulated day.")

    traffic_files = ensure_background_traffic()
    scenarios = build_scenario_matrix(traffic_files, journeys_per_day)

    total_expected = len(scenarios) * journeys_per_day
    logging.info(f"Starting {len(scenarios)} runs (~{total_expected} total bus travels)...")

    # Use up to 4 parallel workers so CPU/RAM stay responsive in background
    max_workers = min(4, os.cpu_count() or 2)

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(execute_worker, sc): sc["run_id"] for sc in scenarios}
        for future in as_completed(futures):
            run_id = futures[future]
            try:
                result = future.result()
                logging.info(result)
            except Exception as exc:
                logging.error(f"FAILED {run_id}: {exc}")

    # Write a master manifest indexing all recorded runs
    manifest = [
        f for f in sorted(os.listdir(OUTPUT_DIR))
        if f.endswith("_metadata.json")
    ]
    with open(os.path.join(OUTPUT_DIR, "manifest.json"), "w", encoding="utf-8") as mf:
        json.dump({"total_runs": len(manifest), "runs": manifest}, mf, indent=2)

    logging.info("Batch generation finished successfully.")


if __name__ == "__main__":
    main()