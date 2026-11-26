"""Unit and integration tests for T002: SUMO/TraCI worker, SourceAdapter, and Replay player."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import traci
from pydantic import ValidationError

from backend.app.adapter import (
    CanonicalVehicleState,
    ReplaySourceAdapter,
    SourceAdapter,
    SumoSourceAdapter,
)
from backend.app.simulation.manifest_loader import (
    DEFAULT_CANONICAL_NETWORK_DIR,
    DEFAULT_FIXTURE_NETWORK_DIR,
)
from backend.app.simulation.replay import RecordedJourneyPlayer
from backend.app.simulation.traci_runner import (
    ObservedStopArrival,
    RawVehicleState,
    SimulationRunConfig,
    SimulationRunResult,
    SimulationTimeoutError,
    SimulationValidationError,
    run_sumo_simulation,
)


def _list_sumo_pids() -> set[int]:
    """Return currently running headless `sumo.exe` / `sumo` PIDs on the host."""
    if sys.platform == "win32":
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq sumo.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        pids: set[int] = set()
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith('"sumo.exe"'):
                parts = [p.strip('"') for p in line.split(",")]
                if len(parts) >= 2 and parts[1].isdigit():
                    pids.add(int(parts[1]))
        return pids
    return set()


def _make_fixture_config(**overrides: Any) -> SimulationRunConfig:
    params: dict[str, Any] = {
        "network_dir": DEFAULT_FIXTURE_NETWORK_DIR,
        "journey_id": "J-TEST-001",
        "scenario_id": "SCEN-BASE",
        "execution_id": "EXEC-001",
        "demand_profile": "campus_rush",
        "departure_time_s": 30,
        "random_seed": 42,
        "intervention_stop_id": None,
        "stop_hold_s": 0,
        "traffic_multiplier": 1.0,
        "snapshot_interval_s": 1,
        "timeout_s": 120,
    }
    params.update(overrides)
    return SimulationRunConfig(**params)


def _strip_wall_clock(snapshots: list[RawVehicleState]) -> list[dict[str, Any]]:
    stripped: list[dict[str, Any]] = []
    for snap in snapshots:
        data = snap.model_dump()
        data.pop("worker_generated_at_ms")
        stripped.append(data)
    return stripped


@pytest.fixture(scope="module")
def sample_baseline_result() -> SimulationRunResult:
    """Shared deterministic baseline run on `sample_line9` reused by read-only assertions."""
    return run_sumo_simulation(_make_fixture_config())


class TestSimulationValidation:
    @pytest.mark.parametrize(
        ("field", "bad_value"),
        [
            ("stop_hold_s", -1),
            ("stop_hold_s", 901),
            ("stop_hold_s", 12.5),
            ("stop_hold_s", True),
            ("traffic_multiplier", 0.0),
            ("traffic_multiplier", 0.49),
            ("traffic_multiplier", 2.51),
            ("traffic_multiplier", float("nan")),
            ("traffic_multiplier", float("inf")),
            ("traffic_multiplier", True),
            ("demand_profile", "super_peak"),
            ("departure_time_s", -5),
            ("random_seed", -1),
            ("snapshot_interval_s", 0),
        ],
    )
    def test_config_rejects_out_of_bounds_and_invalid_types(
        self, field: str, bad_value: Any
    ) -> None:
        overrides: dict[str, Any] = {field: bad_value}
        if field == "stop_hold_s":
            overrides["intervention_stop_id"] = "P02"
        with pytest.raises(ValidationError):
            _make_fixture_config(**overrides)

    def test_config_requires_intervention_stop_when_stop_hold_positive(self) -> None:
        with pytest.raises(ValidationError, match="intervention_stop_id"):
            _make_fixture_config(stop_hold_s=60, intervention_stop_id=None)

    def test_invalid_intervention_stop_raises_before_spawning_sumo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _forbid_start(*_args: Any, **_kwargs: Any) -> None:
            pytest.fail("traci.start must NOT be called when intervention_stop_id is invalid.")

        monkeypatch.setattr(traci, "start", _forbid_start)

        cfg = _make_fixture_config(intervention_stop_id="P99", stop_hold_s=120)
        with pytest.raises(SimulationValidationError, match="P99"):
            run_sumo_simulation(cfg)

    def test_missing_network_dir_raises_before_spawning_sumo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _forbid_start(*_args: Any, **_kwargs: Any) -> None:
            pytest.fail("traci.start must NOT be called when network_dir is invalid.")

        monkeypatch.setattr(traci, "start", _forbid_start)

        cfg = _make_fixture_config(network_dir=tmp_path / "does_not_exist")
        with pytest.raises(ValueError):
            run_sumo_simulation(cfg)


class TestSumoSimulationWorker:
    def test_determinism_identical_configs_produce_identical_trajectories(
        self, sample_baseline_result: SimulationRunResult
    ) -> None:
        streamed: list[RawVehicleState] = []
        second_run = run_sumo_simulation(
            _make_fixture_config(),
            on_snapshot=streamed.append,
        )

        assert sample_baseline_result.completed_cleanly is True
        assert second_run.completed_cleanly is True
        assert sample_baseline_result.network_version == "sample-line9-v1.0"
        assert second_run.sim_duration_s == sample_baseline_result.sim_duration_s

        # Byte-for-byte parity excluding wall-clock worker_generated_at_ms (BR-005)
        assert _strip_wall_clock(second_run.raw_snapshots) == _strip_wall_clock(
            sample_baseline_result.raw_snapshots
        )
        assert [a.model_dump() for a in second_run.observed_arrivals] == [
            a.model_dump() for a in sample_baseline_result.observed_arrivals
        ]

        # Callback received identical monotonic sequence
        assert len(streamed) == len(second_run.raw_snapshots)
        assert [s.sequence_number for s in streamed] == list(range(1, len(streamed) + 1))
        assert streamed[-1].status == "COMPLETED"

    def test_stop_hold_propagates_delay_downstream_with_strict_causal_dwell(
        self, sample_baseline_result: SimulationRunResult
    ) -> None:
        held_run = run_sumo_simulation(
            _make_fixture_config(
                journey_id="J-TEST-HOLD",
                scenario_id="SCEN-HOLD-180",
                execution_id="EXEC-HOLD",
                intervention_stop_id="P02",
                stop_hold_s=180,
            )
        )

        assert held_run.completed_cleanly is True
        base_by_stop: dict[str, ObservedStopArrival] = {
            a.stop_id: a for a in sample_baseline_result.observed_arrivals
        }
        held_by_stop: dict[str, ObservedStopArrival] = {
            a.stop_id: a for a in held_run.observed_arrivals
        }

        assert list(base_by_stop.keys()) == ["P01", "P02", "P03", "P04"]
        assert list(held_by_stop.keys()) == ["P01", "P02", "P03", "P04"]

        # P01 and P02 arrivals are unaffected by holding AT P02
        assert held_by_stop["P01"].observed_arrival_s == base_by_stop["P01"].observed_arrival_s
        assert held_by_stop["P02"].observed_arrival_s == base_by_stop["P02"].observed_arrival_s

        # Dwell at P02 increases by exactly 180 s (10 s -> 190 s)
        assert held_by_stop["P02"].dwell_duration_s == base_by_stop["P02"].dwell_duration_s + 180

        # Downstream stops P03 and P04 arrive significantly later (~180 s propagation)
        assert held_by_stop["P03"].observed_arrival_s >= base_by_stop["P03"].observed_arrival_s + 170
        assert held_by_stop["P04"].observed_arrival_s >= base_by_stop["P04"].observed_arrival_s + 170

        # Causal dwell verification: while stopped at P02, ongoing_stop_elapsed_s increments 1s/tick
        # and last_completed_stop_dwell_s stays at P01's dwell until departure from P02.
        p02_arrival_s = held_by_stop["P02"].observed_arrival_s
        p02_depart_s = p02_arrival_s + held_by_stop["P02"].dwell_duration_s
        p02_stopped_snaps = [
            s
            for s in held_run.raw_snapshots
            if p02_arrival_s <= s.simulation_clock_s < p02_depart_s
        ]
        assert len(p02_stopped_snaps) == held_by_stop["P02"].dwell_duration_s
        assert all(s.status == "AT_STOP" for s in p02_stopped_snaps)
        assert [s.ongoing_stop_elapsed_s for s in p02_stopped_snaps] == list(
            range(len(p02_stopped_snaps))
        )
        assert all(
            s.last_completed_stop_dwell_s == held_by_stop["P01"].dwell_duration_s
            for s in p02_stopped_snaps
        )

        # Immediately upon departure from P02, last_completed_stop_dwell_s latches to 190 s
        p02_depart_snap = next(
            s for s in held_run.raw_snapshots if s.simulation_clock_s == p02_depart_s
        )
        assert p02_depart_snap.status == "IN_TRANSIT"
        assert p02_depart_snap.ongoing_stop_elapsed_s == 0
        assert p02_depart_snap.last_completed_stop_dwell_s == held_by_stop["P02"].dwell_duration_s

    @pytest.mark.parametrize("multiplier", [0.5, 1.8])
    def test_traffic_multiplier_executes_cleanly_without_dropping_or_duplicating_bus(
        self, multiplier: float
    ) -> None:
        result = run_sumo_simulation(
            _make_fixture_config(
                journey_id=f"J-TEST-TM-{multiplier}",
                traffic_multiplier=multiplier,
                demand_profile="heavy_congestion",
                random_seed=99,
            )
        )
        assert result.completed_cleanly is True
        assert [a.stop_id for a in result.observed_arrivals] == ["P01", "P02", "P03", "P04"]
        assert all(s.vehicle_id == "bus_line9_0" for s in result.raw_snapshots)

    def test_snapshot_interval_subsamples_while_preserving_exact_arrivals(
        self, sample_baseline_result: SimulationRunResult
    ) -> None:
        subsampled = run_sumo_simulation(_make_fixture_config(snapshot_interval_s=5))
        assert subsampled.completed_cleanly is True
        assert len(subsampled.raw_snapshots) < len(sample_baseline_result.raw_snapshots) // 3
        assert [a.model_dump() for a in subsampled.observed_arrivals] == [
            a.model_dump() for a in sample_baseline_result.observed_arrivals
        ]

    def test_canonical_line9_circular_route_records_both_p01_and_p22(self) -> None:
        """Verify 22-stop canonical Line 9 tracks both P01 and P22 for reused gtfs_lra_0096."""
        result = run_sumo_simulation(
            SimulationRunConfig(
                network_dir=DEFAULT_CANONICAL_NETWORK_DIR,
                journey_id="J-L9-CANON-001",
                scenario_id="SCEN-L9-BASE",
                execution_id="EXEC-L9-001",
                demand_profile="off_peak",
                departure_time_s=25790,
                random_seed=7,
                traffic_multiplier=1.0,
                snapshot_interval_s=5,
                timeout_s=120,
            )
        )
        assert result.completed_cleanly is True
        assert result.network_version == "line9-v1.0"
        assert len(result.observed_arrivals) == 22
        assert [a.sequence_index for a in result.observed_arrivals] == list(range(22))
        assert result.observed_arrivals[0].stop_id == "P01"
        assert result.observed_arrivals[-1].stop_id == "P22"
        assert result.observed_arrivals[0].sumo_bus_stop_id == "gtfs_lra_0096"
        assert result.observed_arrivals[-1].sumo_bus_stop_id == "gtfs_lra_0096"
        assert (
            result.observed_arrivals[-1].observed_arrival_s
            > result.observed_arrivals[0].observed_arrival_s
        )

    def test_traci_cleanup_on_callback_exception_leaves_no_orphaned_sumo_processes(
        self,
    ) -> None:
        pids_before = _list_sumo_pids()
        seen_count = 0

        def _failing_callback(_snap: RawVehicleState) -> None:
            nonlocal seen_count
            seen_count += 1
            if seen_count >= 5:
                raise RuntimeError("Simulated consumer crash mid-run")

        with pytest.raises(RuntimeError, match="Simulated consumer crash"):
            run_sumo_simulation(_make_fixture_config(), on_snapshot=_failing_callback)

        assert not traci.isLoaded()
        assert _list_sumo_pids() - pids_before == set()

    def test_traci_timeout_closes_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pids_before = _list_sumo_pids()
        calls = 0

        def _advancing_monotonic() -> float:
            nonlocal calls
            calls += 1
            return 0.0 if calls <= 2 else 999.0

        monkeypatch.setattr(time, "monotonic", _advancing_monotonic)

        with pytest.raises(SimulationTimeoutError, match="exceeded timeout"):
            run_sumo_simulation(_make_fixture_config(timeout_s=1))

        assert not traci.isLoaded()
        assert _list_sumo_pids() - pids_before == set()


class TestSourceAdapterParity:
    def test_sumo_and_replay_adapters_produce_identical_canonical_schema(
        self, sample_baseline_result: SimulationRunResult
    ) -> None:
        sumo_adapter = SumoSourceAdapter()
        replay_adapter = ReplaySourceAdapter()

        assert isinstance(sumo_adapter, SourceAdapter)
        assert isinstance(replay_adapter, SourceAdapter)

        raw_sample = sample_baseline_result.raw_snapshots[10]
        from_sumo = sumo_adapter.adapt(raw_sample)
        from_replay_model = replay_adapter.adapt(from_sumo, playback_state="PAUSED")
        from_replay_dict = replay_adapter.adapt(from_sumo.model_dump(), playback_state="PAUSED")

        assert isinstance(from_sumo, CanonicalVehicleState)
        assert isinstance(from_replay_model, CanonicalVehicleState)
        assert isinstance(from_replay_dict, CanonicalVehicleState)

        sumo_dump = from_sumo.model_dump()
        replay_dump = from_replay_model.model_dump()
        assert set(sumo_dump.keys()) == set(replay_dump.keys())
        assert from_replay_model.model_dump() == from_replay_dict.model_dump()

        # Verify provenance differs only where semantically required
        assert from_sumo.source == "SIMULATION"
        assert from_replay_model.source == "RECORDED_REPLAY"
        assert from_sumo.playback_state == "PLAYING"
        assert from_replay_model.playback_state == "PAUSED"

        for shared_key in (
            "journey_id",
            "route_id",
            "scenario_id",
            "execution_id",
            "sequence_number",
            "simulation_clock_s",
            "vehicle_id",
            "status",
            "edge_id",
            "lane_position_m",
            "distance_travelled_m",
            "speed_mps",
            "current_segment_index",
            "last_visited_stop_id",
            "next_stop_id",
            "ongoing_stop_elapsed_s",
            "last_completed_stop_dwell_s",
            "demand_profile",
        ):
            assert sumo_dump[shared_key] == replay_dump[shared_key]

        # Completed raw snapshot maps to playback_state == "STOPPED"
        final_canonical = sumo_adapter.adapt(sample_baseline_result.raw_snapshots[-1])
        assert final_canonical.status == "COMPLETED"
        assert final_canonical.playback_state == "STOPPED"

    def test_adapters_reject_unsupported_payload_types(self) -> None:
        with pytest.raises(TypeError):
            SumoSourceAdapter().adapt({"not": "a_raw_state"})  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            ReplaySourceAdapter().adapt(12345)  # type: ignore[arg-type]


class TestRecordedJourneyPlayer:
    def test_rejects_empty_snapshots(self) -> None:
        with pytest.raises(ValueError, match="at least one snapshot"):
            RecordedJourneyPlayer([])

    def test_play_pause_tick_step_and_restart_semantics(
        self, sample_baseline_result: SimulationRunResult
    ) -> None:
        canonical_stream = SumoSourceAdapter().adapt_many(sample_baseline_result.raw_snapshots[:10])
        player = RecordedJourneyPlayer(canonical_stream, execution_id="replay-init-001")

        # Initial paused state (BR-003)
        s0 = player.current_state()
        assert s0.source == "RECORDED_REPLAY"
        assert s0.playback_state == "PAUSED"
        assert s0.execution_id == "replay-init-001"
        assert s0.sequence_number == 1
        assert s0.simulation_clock_s == canonical_stream[0].simulation_clock_s

        # While PAUSED, tick() freezes simulation_clock_s and sequence_number (BR-003)
        frozen = player.tick()
        assert frozen.simulation_clock_s == s0.simulation_clock_s
        assert frozen.sequence_number == 1
        assert frozen.playback_state == "PAUSED"

        # play() + tick() advances clock and sequence monotonically
        playing = player.play()
        assert playing.playback_state == "PLAYING"
        assert playing.simulation_clock_s == s0.simulation_clock_s

        s1 = player.tick()
        assert s1.simulation_clock_s == canonical_stream[1].simulation_clock_s
        assert s1.sequence_number == 2
        assert s1.execution_id == "replay-init-001"

        # pause() freezes simulation_clock_s again
        paused = player.pause()
        assert paused.playback_state == "PAUSED"
        assert paused.simulation_clock_s == s1.simulation_clock_s
        assert player.tick().simulation_clock_s == s1.simulation_clock_s
        assert player.current_state().simulation_clock_s == s1.simulation_clock_s

        # Explicit step() advances frame & increments sequence_number even while paused
        stepped = player.step(steps=2)
        assert stepped.simulation_clock_s == canonical_stream[3].simulation_clock_s
        assert stepped.sequence_number == 3
        assert stepped.playback_state == "PAUSED"

        # Stepping past the end clamps to the final snapshot and sets STOPPED
        ended = player.step(steps=50)
        assert ended.simulation_clock_s == canonical_stream[-1].simulation_clock_s
        assert ended.playback_state == "STOPPED"
        assert player.is_finished is True
        assert player.play().playback_state == "STOPPED"

        # restart() generates a new execution_id and resets sequence_number to 1 (BR-009)
        restarted = player.restart()
        assert restarted.execution_id != "replay-init-001"
        assert restarted.sequence_number == 1
        assert restarted.simulation_clock_s == canonical_stream[0].simulation_clock_s
        assert restarted.playback_state == "PLAYING"
        assert player.current_index == 0

        # Invalid step counts rejected
        with pytest.raises(ValueError, match="positive integer"):
            player.step(0)
        with pytest.raises(ValueError, match="positive integer"):
            player.step(True)  # type: ignore[arg-type]
