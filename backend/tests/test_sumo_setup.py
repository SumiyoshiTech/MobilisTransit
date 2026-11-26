"""Unit and integration tests for T001: SUMO binary resolution, manifests, and dry runs."""

from __future__ import annotations

import importlib.metadata
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.simulation import manifest_loader
from backend.app.simulation.manifest_loader import (
    DEFAULT_CANONICAL_NETWORK_DIR,
    DEFAULT_FIXTURE_NETWORK_DIR,
    REPO_ROOT,
    ManifestValidationError,
    NetworkManifest,
    load_and_validate_manifest,
    resolve_active_network_dir,
)
from backend.app.simulation.sumo_env import (
    SumoNotFoundError,
    ensure_sumo_home_env,
    resolve_sumo_binary,
)


def _write_minimal_fixture(base_dir: Path, manifest_override: dict | None = None) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "net.net.xml").write_text("<net/>", encoding="utf-8")
    (base_dir / "stops.add.xml").write_text(
        '<additional><busStop id="bs_1" lane="e0_0" startPos="0" endPos="10"/>'
        '<busStop id="bs_2" lane="e1_0" startPos="0" endPos="10"/></additional>',
        encoding="utf-8",
    )
    (base_dir / "sim.sumocfg").write_text("<configuration/>", encoding="utf-8")

    payload: dict = {
        "route_id": "LINE-9",
        "route_code": "L9",
        "route_name": "Test Loop Route",
        "network_version": "test-v1",
        "sumocfg": "sim.sumocfg",
        "net_file": "net.net.xml",
        "route_file": None,
        "additional_files": ["stops.add.xml"],
        "bus_vehicle_id": "bus_0",
        "bus_vehicle_prefix": "bus_",
        "bus_route_id": "route_0",
        "calibration_notes": "test",
        "stops": [
            {
                "stop_id": "P01",
                "sumo_bus_stop_id": "bs_1",
                "name": "Origin",
                "sequence_index": 0,
                "distance_from_start_m": 0.0,
                "x_norm": 0.1,
                "y_norm": 0.2,
                "reference_offset_s": 0,
                "ar_marker_id": "marker_p01",
                "ar_marker_width_m": 0.18,
            },
            {
                "stop_id": "P02",
                "sumo_bus_stop_id": "bs_2",
                "name": "Midpoint",
                "sequence_index": 1,
                "distance_from_start_m": 500.0,
                "x_norm": 0.5,
                "y_norm": 0.5,
                "reference_offset_s": 120,
                "ar_marker_id": "marker_p02",
                "ar_marker_width_m": 0.18,
            },
            {
                "stop_id": "P03",
                "sumo_bus_stop_id": "bs_1",
                "name": "Origin (Return Loop)",
                "sequence_index": 2,
                "distance_from_start_m": 1000.0,
                "x_norm": 0.1,
                "y_norm": 0.2,
                "reference_offset_s": None,
                "ar_marker_id": "marker_p03",
                "ar_marker_width_m": 0.18,
            },
        ],
    }
    if manifest_override:
        payload.update(manifest_override)
    (base_dir / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    return base_dir


class TestWorkspaceSafeguards:
    def test_sumo_directory_does_not_shadow_eclipse_sumo(self) -> None:
        sumo_dir = REPO_ROOT / "sumo"
        assert not (sumo_dir / "__init__.py").exists()
        top_level_py = list(sumo_dir.glob("*.py"))
        assert top_level_py == [], f"Unexpected top-level .py files in sumo/: {top_level_py}"

        for canonical_file in (
            "linha9.sumocfg",
            "leiria.net.xml.gz",
            "pt_vtypes.xml",
            "gtfs_pt_stops.add.xml",
            "gtfs_pt_vehicles_L9.add.xml",
        ):
            assert (sumo_dir / canonical_file).is_file(), f"Missing canonical asset: {canonical_file}"

    def test_traci_and_sumolib_import_cleanly(self) -> None:
        import sumolib
        import traci

        assert hasattr(traci, "start")
        assert hasattr(sumolib, "net")


class TestSumoEnv:
    def test_resolve_sumo_binary_real_environment(self) -> None:
        binary = resolve_sumo_binary(gui=False)
        assert binary.is_file()
        assert binary.name.lower() in {"sumo", "sumo.exe"}

        home = ensure_sumo_home_env()
        assert home.is_dir()

    def test_prefers_sumo_home_over_path_and_metadata(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_home = tmp_path / "custom_sumo"
        bin_dir = fake_home / "bin"
        bin_dir.mkdir(parents=True)
        exe_name = "sumo.exe" if sys.platform == "win32" else "sumo"
        fake_binary = bin_dir / exe_name
        fake_binary.write_text("", encoding="utf-8")

        monkeypatch.setenv("SUMO_HOME", str(fake_home))
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        resolved = resolve_sumo_binary(gui=False)
        assert resolved == fake_binary.resolve()
        assert ensure_sumo_home_env() == fake_home.resolve()

    def test_falls_back_to_shutil_which(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exe_name = "sumo.exe" if sys.platform == "win32" else "sumo"
        fake_bin = tmp_path / "bin" / exe_name
        fake_bin.parent.mkdir(parents=True)
        fake_bin.write_text("", encoding="utf-8")

        monkeypatch.delenv("SUMO_HOME", raising=False)
        monkeypatch.setattr(shutil, "which", lambda _name: str(fake_bin))

        resolved = resolve_sumo_binary(gui=False)
        assert resolved == fake_bin.resolve()

    def test_falls_back_to_eclipse_sumo_metadata_without_importing_sumo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exe_name = "sumo.exe" if sys.platform == "win32" else "sumo"
        fake_site = tmp_path / "site-packages"
        fake_bin = fake_site / "sumo" / "bin" / exe_name
        fake_bin.parent.mkdir(parents=True)
        fake_bin.write_text("", encoding="utf-8")

        rel_entry = Path("sumo") / "bin" / exe_name
        fake_dist = SimpleNamespace(
            files=[rel_entry],
            locate_file=lambda rel: fake_site / Path(rel),
        )

        monkeypatch.delenv("SUMO_HOME", raising=False)
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        monkeypatch.setattr(importlib.metadata, "distribution", lambda _name: fake_dist)

        resolved = resolve_sumo_binary(gui=False)
        assert resolved == fake_bin.resolve()

        home = ensure_sumo_home_env()
        assert home == (fake_site / "sumo").resolve()

    def test_raises_sumo_not_found_error_with_instructions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SUMO_HOME", raising=False)
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        def _missing(_name: str) -> None:
            raise importlib.metadata.PackageNotFoundError("eclipse-sumo")

        monkeypatch.setattr(importlib.metadata, "distribution", _missing)

        with pytest.raises(SumoNotFoundError) as exc_info:
            resolve_sumo_binary(gui=False)
        msg = str(exc_info.value)
        assert "SUMO_HOME" in msg
        assert "eclipse-sumo" in msg


class TestManifestLoader:
    def test_loads_canonical_line9_manifest(self) -> None:
        manifest = load_and_validate_manifest(DEFAULT_CANONICAL_NETWORK_DIR)
        assert manifest.route_id == "LINE-9"
        assert manifest.net_file == "leiria.net.xml.gz"
        assert manifest.route_file is None
        assert len(manifest.stops) == 22
        assert manifest.stops[0].stop_id == "P01"
        assert manifest.stops[-1].stop_id == "P22"
        # Circular loop terminal repeats gtfs_lra_0096 at start and end
        assert manifest.stops[0].sumo_bus_stop_id == "gtfs_lra_0096"
        assert manifest.stops[-1].sumo_bus_stop_id == "gtfs_lra_0096"
        assert manifest.stops[0].reference_offset_s == 0
        assert manifest.stops[-1].reference_offset_s == 2400
        assert [s.sequence_index for s in manifest.stops] == list(range(22))

    def test_loads_synthetic_fixture_manifest(self) -> None:
        manifest = load_and_validate_manifest(DEFAULT_FIXTURE_NETWORK_DIR)
        assert manifest.route_id == "LINE-9"
        assert manifest.net_file == "sample_line9.net.xml"
        assert manifest.route_file == "sample_line9.rou.xml"
        assert manifest.bus_vehicle_id == "bus_line9_0"
        assert len(manifest.stops) == 4
        assert [s.stop_id for s in manifest.stops] == ["P01", "P02", "P03", "P04"]
        # P04 intentionally omits reference timetable to exercise BR-001
        assert manifest.stops[3].reference_offset_s is None

    def test_manifest_schema_json_matches_pydantic_model(self) -> None:
        schema_path = DEFAULT_CANONICAL_NETWORK_DIR / "manifest.schema.json"
        assert schema_path.is_file()
        schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
        expected = NetworkManifest.model_json_schema()
        assert schema_data["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema_data["properties"] == expected["properties"]
        assert schema_data["required"] == expected["required"]

    def test_supports_loop_terminal_reuse(self, tmp_path: Path) -> None:
        fixture_dir = _write_minimal_fixture(tmp_path / "loop_ok")
        manifest = load_and_validate_manifest(fixture_dir)
        assert manifest.stops[0].sumo_bus_stop_id == manifest.stops[2].sumo_bus_stop_id == "bs_1"

    def test_resolve_active_network_dir_default_preferred_and_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        assert resolve_active_network_dir() == DEFAULT_CANONICAL_NETWORK_DIR.resolve()
        assert (
            resolve_active_network_dir(DEFAULT_FIXTURE_NETWORK_DIR)
            == DEFAULT_FIXTURE_NETWORK_DIR.resolve()
        )

        # Fallback when canonical dir lacks manifest.json
        empty_canonical = tmp_path / "missing_canonical"
        empty_canonical.mkdir()
        monkeypatch.setattr(manifest_loader, "DEFAULT_CANONICAL_NETWORK_DIR", empty_canonical)

        with caplog.at_level(logging.WARNING):
            resolved = resolve_active_network_dir()

        assert resolved == DEFAULT_FIXTURE_NETWORK_DIR.resolve()
        assert "fixture mode" in caplog.text.lower()

    def test_rejects_out_of_order_sequence_index(self, tmp_path: Path) -> None:
        fixture_dir = _write_minimal_fixture(
            tmp_path / "bad_seq",
            manifest_override={
                "stops": [
                    {
                        "stop_id": "P01",
                        "sumo_bus_stop_id": "bs_1",
                        "name": "Stop 1",
                        "sequence_index": 0,
                        "distance_from_start_m": 0.0,
                        "x_norm": 0.1,
                        "y_norm": 0.1,
                    },
                    {
                        "stop_id": "P02",
                        "sumo_bus_stop_id": "bs_2",
                        "name": "Stop 2",
                        "sequence_index": 2,
                        "distance_from_start_m": 200.0,
                        "x_norm": 0.5,
                        "y_norm": 0.5,
                    },
                ]
            },
        )
        with pytest.raises(ManifestValidationError, match="sequence_index"):
            load_and_validate_manifest(fixture_dir)

    def test_rejects_duplicate_stop_id(self, tmp_path: Path) -> None:
        fixture_dir = _write_minimal_fixture(
            tmp_path / "dup_stop",
            manifest_override={
                "stops": [
                    {
                        "stop_id": "P01",
                        "sumo_bus_stop_id": "bs_1",
                        "name": "Stop 1",
                        "sequence_index": 0,
                        "distance_from_start_m": 0.0,
                        "x_norm": 0.1,
                        "y_norm": 0.1,
                    },
                    {
                        "stop_id": "P01",
                        "sumo_bus_stop_id": "bs_2",
                        "name": "Stop 2",
                        "sequence_index": 1,
                        "distance_from_start_m": 200.0,
                        "x_norm": 0.5,
                        "y_norm": 0.5,
                    },
                ]
            },
        )
        with pytest.raises(ManifestValidationError, match="Duplicate stop_id"):
            load_and_validate_manifest(fixture_dir)

    def test_rejects_decreasing_distance(self, tmp_path: Path) -> None:
        fixture_dir = _write_minimal_fixture(
            tmp_path / "bad_dist",
            manifest_override={
                "stops": [
                    {
                        "stop_id": "P01",
                        "sumo_bus_stop_id": "bs_1",
                        "name": "Stop 1",
                        "sequence_index": 0,
                        "distance_from_start_m": 500.0,
                        "x_norm": 0.1,
                        "y_norm": 0.1,
                    },
                    {
                        "stop_id": "P02",
                        "sumo_bus_stop_id": "bs_2",
                        "name": "Stop 2",
                        "sequence_index": 1,
                        "distance_from_start_m": 200.0,
                        "x_norm": 0.5,
                        "y_norm": 0.5,
                    },
                ]
            },
        )
        with pytest.raises(ManifestValidationError, match="non-decreasing"):
            load_and_validate_manifest(fixture_dir)

    def test_rejects_missing_bus_stop_id_in_xml(self, tmp_path: Path) -> None:
        fixture_dir = _write_minimal_fixture(
            tmp_path / "missing_bs",
            manifest_override={
                "stops": [
                    {
                        "stop_id": "P01",
                        "sumo_bus_stop_id": "bs_1",
                        "name": "Stop 1",
                        "sequence_index": 0,
                        "distance_from_start_m": 0.0,
                        "x_norm": 0.1,
                        "y_norm": 0.1,
                    },
                    {
                        "stop_id": "P02",
                        "sumo_bus_stop_id": "bs_nonexistent",
                        "name": "Missing Stop",
                        "sequence_index": 1,
                        "distance_from_start_m": 250.0,
                        "x_norm": 0.5,
                        "y_norm": 0.5,
                    },
                ]
            },
        )
        with pytest.raises(ManifestValidationError, match="bs_nonexistent"):
            load_and_validate_manifest(fixture_dir)

    @pytest.mark.parametrize(
        ("field_name", "bad_value"),
        [
            ("sumocfg", "../escape.sumocfg"),
            ("net_file", "../../escape.net.xml"),
            ("route_file", "../escape.rou.xml"),
            ("additional_files", ["../escape.add.xml"]),
            ("net_file", "/abs/path/escape.net.xml"),
        ],
    )
    def test_rejects_path_traversal_and_absolute_paths(
        self, tmp_path: Path, field_name: str, bad_value: object
    ) -> None:
        fixture_dir = _write_minimal_fixture(
            tmp_path / f"traversal_{field_name}",
            manifest_override={field_name: bad_value},
        )
        with pytest.raises(ManifestValidationError):
            load_and_validate_manifest(fixture_dir)

    def test_rejects_out_of_range_normalized_coordinates(self, tmp_path: Path) -> None:
        fixture_dir = _write_minimal_fixture(
            tmp_path / "bad_coords",
            manifest_override={
                "stops": [
                    {
                        "stop_id": "P01",
                        "sumo_bus_stop_id": "bs_1",
                        "name": "Stop 1",
                        "sequence_index": 0,
                        "distance_from_start_m": 0.0,
                        "x_norm": 1.2,
                        "y_norm": 0.1,
                    },
                    {
                        "stop_id": "P02",
                        "sumo_bus_stop_id": "bs_2",
                        "name": "Stop 2",
                        "sequence_index": 1,
                        "distance_from_start_m": 250.0,
                        "x_norm": 0.5,
                        "y_norm": 0.5,
                    },
                ]
            },
        )
        with pytest.raises(ManifestValidationError):
            load_and_validate_manifest(fixture_dir)


class TestHeadlessSumoDryRuns:
    def test_synthetic_fixture_runs_cleanly_headless(self) -> None:
        ensure_sumo_home_env()
        sumo_bin = resolve_sumo_binary(gui=False)
        cfg = DEFAULT_FIXTURE_NETWORK_DIR / "sample_line9.sumocfg"

        completed = subprocess.run(
            [str(sumo_bin), "-c", str(cfg), "--no-step-log"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert completed.returncode == 0, (
            f"Fixture SUMO run failed (rc={completed.returncode}):\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )

    def test_canonical_line9_windowed_dry_run_exits_zero(self) -> None:
        ensure_sumo_home_env()
        sumo_bin = resolve_sumo_binary(gui=False)
        cfg = DEFAULT_CANONICAL_NETWORK_DIR / "linha9.sumocfg"

        completed = subprocess.run(
            [
                str(sumo_bin),
                "-c",
                str(cfg),
                "--begin",
                "25780",
                "--end",
                "26000",
                "--no-step-log",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert completed.returncode == 0, (
            f"Canonical Line 9 SUMO run failed (rc={completed.returncode}):\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
