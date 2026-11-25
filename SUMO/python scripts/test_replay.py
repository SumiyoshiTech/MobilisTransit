from adapter.source_adapter import RecordedReplayAdapter

# speed_factor=1.0 plays in real time; 10.0 plays 10x faster; 0 streams immediately
replay = RecordedReplayAdapter(
    ndjson_path="recorded_runs/L9_HOLD_180S_02_snapshots.ndjson",
    speed_factor=1000.0
)

for state in replay.stream():
    print(f"[{state.sim_time_sec}s] {state.journey_id} | Speed: {state.speed_mps} m/s | Next Stop: {state.next_stop_id}")