import os
import sys
import json
import copy
import random
import hashlib
import logging
import xml.etree.ElementTree as ET
import traci
import sumolib

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))
else:
    sys.exit("Define SUMO_HOME variavel")

from adapter.source_adapter import LiveSumoAdapter


def get_file_hash(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def atomic_write_json(path: str, data, **dump_kwargs):
    """Write JSON to <path>.tmp and rename, so readers never see a partial file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, **dump_kwargs)
    os.replace(tmp, path)


def apply_departure_offset(base_veh_xml: str, out_veh_xml: str, offset_sec: int) -> int:
    """Create a temporary copy of Linha 9 travels with a departure offset.

    Returns how many departures were clamped to 0 (negative offsets can't
    move a trip before t=0), so the caller can record it in the metadata.
    """
    tree = ET.parse(base_veh_xml)
    root = tree.getroot()
    clamped = 0
    for veh in root.findall("vehicle"):
        if "depart" in veh.attrib:
            raw = float(veh.attrib["depart"]) + offset_sec
            if raw < 0.0:
                clamped += 1
            veh.attrib["depart"] = f"{max(0.0, raw):.1f}"
    tree.write(out_veh_xml, encoding="utf-8", xml_declaration=True)
    return clamped


def scale_background_traffic(src_xml: str, dst_xml: str, multiplier: float, seed: int) -> dict:
    """Scale ONLY the background traffic (trips/vehicles in src_xml).

    SUMO's --scale would also duplicate/drop the Line 9 buses, so the
    multiplier is applied here instead, with a seeded RNG for reproducibility.
      - multiplier < 1: each trip is kept with probability = multiplier
      - multiplier > 1: every trip is kept, plus extra jittered copies
    Output is re-sorted by departure time, as SUMO requires.
    """
    tree = ET.parse(src_xml)
    root = tree.getroot()
    rng = random.Random(seed)

    demand_tags = ("trip", "vehicle")
    others = [e for e in root if e.tag not in demand_tags]
    originals = [e for e in root if e.tag in demand_tags]

    kept = []
    for item in originals:
        if multiplier < 1.0:
            if rng.random() < multiplier:
                kept.append(item)
            continue

        kept.append(item)
        extra = multiplier - 1.0
        n_extra = int(extra) + (1 if rng.random() < (extra - int(extra)) else 0)
        for k in range(1, n_extra + 1):
            clone = copy.deepcopy(item)
            clone.attrib["id"] = f"{item.attrib['id']}.x{k}"
            try:
                clone.attrib["depart"] = f"{float(item.attrib['depart']) + rng.uniform(0.0, 5.0):.2f}"
            except (KeyError, ValueError):
                pass
            kept.append(clone)

    def depart_key(e):
        try:
            return float(e.attrib.get("depart", 0.0))
        except ValueError:
            return 0.0

    kept.sort(key=depart_key)
    root[:] = others + kept
    tree.write(dst_xml, encoding="utf-8", xml_declaration=True)

    return {
        "requested_multiplier": multiplier,
        "original_vehicles": len(originals),
        "scaled_vehicles": len(kept),
    }


def run_experiment(config: dict, output_dir: str = "recorded_runs"):
    os.makedirs(output_dir, exist_ok=True)
    run_id = config["run_id"]
    net_path = config["net_file"]
    net_version = get_file_hash(net_path)
    multiplier = float(config.get("traffic_multiplier", 1.0))

    # 1. Departure offset (if configured)
    veh_xml = config["l9_vehicles_file"]
    clamped_departures = 0
    offset = config.get("departure_offset_sec", 0)
    if offset != 0:
        veh_xml = os.path.join(output_dir, f"{run_id}_vehicles.add.xml")
        clamped_departures = apply_departure_offset(config["l9_vehicles_file"], veh_xml, offset)

    # 2. Additional files; background traffic is scaled separately from the buses
    add_files = [config["vtypes_file"], config["stops_file"], veh_xml]
    background_scaling = None
    if config.get("background_traffic_file"):
        bg_path = config["background_traffic_file"]
        if multiplier != 1.0:
            bg_path = os.path.join(output_dir, f"{run_id}_background.rou.xml")
            background_scaling = scale_background_traffic(
                config["background_traffic_file"], bg_path, multiplier, config["seed"]
            )
        add_files.append(bg_path)

    # 3. Deterministic SUMO command. NOTE: no --scale here (it would also
    #    scale the Line 9 buses); the multiplier is applied to background only.
    sumo_cmd = [
        "sumo",
        "-n", net_path,
        "-a", ",".join(add_files),
        "-b", str(config.get("begin_sec", 25200)),
        "-e", str(config.get("end_sec", 72000)),
        "--seed", str(config["seed"]),
        "--no-step-log",
        "--step-length", "5.0",
        "--no-warnings",
    ]

    metadata = {
        "run_id": run_id,
        "network_file": net_path,
        "network_version_hash": net_version,
        "demand_profile": config["demand_profile"],
        "traffic_multiplier": multiplier,
        "background_scaling": background_scaling,
        "seed": config["seed"],
        "departure_offset_sec": offset,
        "departures_clamped_to_zero": clamped_departures,
        "disturbance": config.get("disturbance"),  # e.g. {"stop_index": 5, "hold_sec": 180}
        "calibration_notes": config.get("calibration_notes", "Baseline GTFS dwell times"),
    }

    snapshots_path = os.path.join(output_dir, f"{run_id}_snapshots.ndjson")
    snapshots_tmp = snapshots_path + ".tmp"
    arrivals_path = os.path.join(output_dir, f"{run_id}_arrivals.json")
    meta_path = os.path.join(output_dir, f"{run_id}_metadata.json")

    stop_arrivals = {}        # {journey_id: {stop_id: arrival_timestamp}}
    visited_stops = {}        # {journey_id: set(stop_ids)}
    disturbance_checked = set()
    disturbance_log = {}      # {journey_id: what actually happened}

    net = sumolib.net.readNet(net_path)
    traci.start(sumo_cmd)
    succeeded = False

    try:
        adapter = LiveSumoAdapter(traci, net, run_id=run_id, run_metadata=metadata, snapshot_interval=5)

        with open(snapshots_tmp, "w", encoding="utf-8") as snap_file:
            for state in adapter.stream():
                j_id = state.journey_id

                # Inject the stop-hold disturbance once per journey, at first sight
                dist = config.get("disturbance")
                if dist and j_id not in disturbance_checked:
                    disturbance_checked.add(j_id)  # check once, never retry every step
                    try:
                        next_stops = traci.vehicle.getNextStops(j_id)
                        idx = dist["stop_index"]
                        if len(next_stops) > idx:
                            traci.vehicle.setStopParameter(j_id, idx, "duration", str(dist["hold_sec"]))
                            disturbance_log[j_id] = {
                                "applied": True,
                                "stop_index": idx,
                                "stop_id": getattr(next_stops[idx], "stoppingPlaceID", None),
                                "hold_sec": dist["hold_sec"],
                            }
                        else:
                            disturbance_log[j_id] = {
                                "applied": False,
                                "reason": f"only {len(next_stops)} stops remaining",
                            }
                    except traci.TraCIException as exc:
                        disturbance_log[j_id] = {"applied": False, "reason": str(exc)}
                        logging.warning(f"[{run_id}] disturbance failed for {j_id}: {exc}")

                # Record the real arrival timestamp at each stop
                if state.current_stop_id:
                    visited_stops.setdefault(j_id, set())
                    if state.current_stop_id not in visited_stops[j_id]:
                        visited_stops[j_id].add(state.current_stop_id)
                        stop_arrivals.setdefault(j_id, {})[state.current_stop_id] = state.sim_time_sec

                # Write snapshot in canonical format
                snap_file.write(json.dumps(state.to_dict()) + "\n")

        succeeded = True

    finally:
        # Always shut SUMO down so no orphaned process survives a failure
        try:
            traci.close()
        except Exception:
            pass
        # On failure, remove the partial snapshot file so it can't be mistaken for a finished run
        if not succeeded and os.path.exists(snapshots_tmp):
            try:
                os.remove(snapshots_tmp)
            except OSError:
                pass

    # Success path: snapshots first, arrivals next, metadata LAST.
    # The metadata file is the "run is complete" marker.
    os.replace(snapshots_tmp, snapshots_path)
    atomic_write_json(arrivals_path, stop_arrivals, indent=2)

    metadata["disturbance_log"] = disturbance_log
    metadata["journeys_simulated"] = len(stop_arrivals)
    atomic_write_json(meta_path, metadata, indent=2)

    print(f"[OK] {run_id} concluded -> {snapshots_path}")