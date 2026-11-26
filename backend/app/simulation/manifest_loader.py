"""Pydantic v2 manifest schema, path-containment validation, and SUMO network discovery."""

from __future__ import annotations

import gzip
import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CANONICAL_NETWORK_DIR = REPO_ROOT / "sumo"
DEFAULT_FIXTURE_NETWORK_DIR = REPO_ROOT / "sumo" / "fixtures" / "sample_line9"


class ManifestValidationError(ValueError):
    """Raised when a SUMO network manifest fails schema, filesystem, or XML validation."""


def _validate_relative_path_syntax(raw_path: str, field_name: str) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{field_name} must be a non-empty relative path.")
    if "\x00" in raw_path:
        raise ValueError(f"{field_name} must not contain null bytes.")

    win_path = PureWindowsPath(raw_path)
    posix_path = PurePosixPath(raw_path.replace("\\", "/"))
    if (
        Path(raw_path).is_absolute()
        or win_path.is_absolute()
        or bool(win_path.drive)
        or raw_path.startswith(("/", "\\"))
    ):
        raise ValueError(f"{field_name} must be a relative path inside network_dir: {raw_path!r}")
    if ".." in posix_path.parts or ".." in win_path.parts:
        raise ValueError(f"{field_name} must not contain path traversal ('..'): {raw_path!r}")

    return raw_path


class StopManifest(BaseModel):
    """Single ordered bus stop entry in a SUMO route manifest."""

    model_config = ConfigDict(extra="forbid")

    stop_id: str = Field(min_length=1, description="Unique stop code on the route (e.g. P01).")
    sumo_bus_stop_id: str = Field(
        min_length=1,
        description="SUMO <busStop id='...'> identifier (may repeat for circular loop terminals).",
    )
    name: str = Field(min_length=1, description="Human-readable stop name.")
    sequence_index: int = Field(ge=0, description="Zero-based strictly increasing stop order.")
    distance_from_start_m: float = Field(
        ge=0.0,
        description="Cumulative route distance from sequence_index=0 in metres.",
    )
    x_norm: float = Field(ge=0.0, le=1.0, description="Normalized horizontal coordinate [0, 1].")
    y_norm: float = Field(ge=0.0, le=1.0, description="Normalized vertical coordinate [0, 1].")
    reference_offset_s: int | None = Field(
        default=None,
        ge=0,
        description="Published timetable offset from trip start in seconds, or null (BR-001).",
    )
    ar_marker_id: str | None = Field(
        default=None,
        min_length=1,
        description="Printed Augmented Images marker identifier.",
    )
    ar_marker_width_m: float | None = Field(
        default=None,
        gt=0.0,
        description="Physical width of the printed AR marker in metres.",
    )

    @field_validator("stop_id", "sumo_bus_stop_id", "name")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field must not be blank.")
        return value


class NetworkManifest(BaseModel):
    """Canonical SUMO network & route topology manifest (ARCHITECTURE.md §4.1)."""

    model_config = ConfigDict(extra="forbid")

    route_id: str = Field(min_length=1)
    route_code: str = Field(min_length=1)
    route_name: str = Field(min_length=1)
    network_version: str = Field(min_length=1)
    sumocfg: str = Field(min_length=1)
    net_file: str = Field(min_length=1)
    route_file: str | None = None
    additional_files: list[str] = Field(default_factory=list)
    bus_vehicle_id: str = Field(min_length=1)
    bus_vehicle_prefix: str | None = None
    bus_route_id: str = Field(min_length=1)
    calibration_notes: str = ""
    stops: list[StopManifest] = Field(min_length=2)

    @field_validator("sumocfg")
    @classmethod
    def _validate_sumocfg(cls, value: str) -> str:
        _validate_relative_path_syntax(value, "sumocfg")
        if not value.endswith(".sumocfg"):
            raise ValueError(f"sumocfg must end with '.sumocfg': {value!r}")
        return value

    @field_validator("net_file")
    @classmethod
    def _validate_net_file(cls, value: str) -> str:
        _validate_relative_path_syntax(value, "net_file")
        if not value.endswith((".net.xml", ".net.xml.gz")):
            raise ValueError(f"net_file must end with '.net.xml' or '.net.xml.gz': {value!r}")
        return value

    @field_validator("route_file")
    @classmethod
    def _validate_route_file(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_relative_path_syntax(value, "route_file")
        return value

    @field_validator("additional_files")
    @classmethod
    def _validate_additional_files(cls, values: list[str]) -> list[str]:
        for idx, item in enumerate(values):
            _validate_relative_path_syntax(item, f"additional_files[{idx}]")
        return values

    @model_validator(mode="after")
    def _validate_stops_invariants(self) -> NetworkManifest:
        seen_stop_ids: set[str] = set()
        previous_distance: float | None = None

        for expected_index, stop in enumerate(self.stops):
            if stop.stop_id in seen_stop_ids:
                raise ValueError(f"Duplicate stop_id detected in manifest: {stop.stop_id!r}")
            seen_stop_ids.add(stop.stop_id)

            if stop.sequence_index != expected_index:
                raise ValueError(
                    f"sequence_index must be strictly increasing 0..N-1; "
                    f"expected {expected_index} at stop {stop.stop_id!r}, got {stop.sequence_index}."
                )

            if previous_distance is not None and stop.distance_from_start_m < previous_distance:
                raise ValueError(
                    f"distance_from_start_m must be non-decreasing; stop {stop.stop_id!r} "
                    f"has {stop.distance_from_start_m} < previous {previous_distance}."
                )
            previous_distance = stop.distance_from_start_m

        return self


def _resolve_contained_file(network_dir: Path, relative_path: str, field_name: str) -> Path:
    try:
        _validate_relative_path_syntax(relative_path, field_name)
    except ValueError as exc:
        raise ManifestValidationError(str(exc)) from exc

    resolved_base = network_dir.resolve()
    candidate = (resolved_base / relative_path).resolve()
    if not candidate.is_relative_to(resolved_base):
        raise ManifestValidationError(
            f"{field_name} escapes network_dir {resolved_base}: {relative_path!r}"
        )
    if not candidate.is_file():
        raise ManifestValidationError(
            f"Referenced {field_name} does not exist inside {resolved_base}: {relative_path!r}"
        )
    return candidate


def _parse_xml_root(xml_path: Path) -> ET.Element:
    try:
        if xml_path.suffix.lower() == ".gz":
            with gzip.open(xml_path, "rb") as fh:
                return ET.parse(fh).getroot()
        return ET.parse(xml_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ManifestValidationError(f"Failed to parse XML file {xml_path}: {exc}") from exc


def _collect_bus_stop_ids(additional_paths: list[Path]) -> set[str]:
    bus_stop_ids: set[str] = set()
    for path in additional_paths:
        root = _parse_xml_root(path)
        for elem in root.iter("busStop"):
            bus_stop_id = elem.attrib.get("id")
            if bus_stop_id:
                bus_stop_ids.add(bus_stop_id)
    return bus_stop_ids


def load_and_validate_manifest(network_dir: Path) -> NetworkManifest:
    """Load `manifest.json` from `network_dir` and validate schema, file containment, and XML stops."""
    resolved_dir = Path(network_dir).resolve()
    if not resolved_dir.is_dir():
        raise ManifestValidationError(f"Network directory does not exist: {resolved_dir}")

    manifest_path = resolved_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ManifestValidationError(f"Missing manifest.json in network directory: {resolved_dir}")

    try:
        raw_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestValidationError(f"Invalid JSON in {manifest_path}: {exc}") from exc

    try:
        manifest = NetworkManifest.model_validate(raw_data)
    except ValidationError as exc:
        raise ManifestValidationError(f"Manifest schema validation failed: {exc}") from exc

    sumocfg_path = _resolve_contained_file(resolved_dir, manifest.sumocfg, "sumocfg")
    _parse_xml_root(sumocfg_path)
    _resolve_contained_file(resolved_dir, manifest.net_file, "net_file")

    if manifest.route_file is not None:
        _resolve_contained_file(resolved_dir, manifest.route_file, "route_file")

    additional_paths = [
        _resolve_contained_file(resolved_dir, rel_path, f"additional_files[{idx}]")
        for idx, rel_path in enumerate(manifest.additional_files)
    ]

    defined_bus_stops = _collect_bus_stop_ids(additional_paths)
    missing_stops = sorted(
        {stop.sumo_bus_stop_id for stop in manifest.stops} - defined_bus_stops
    )
    if missing_stops:
        raise ManifestValidationError(
            "Manifest references sumo_bus_stop_id(s) missing from additional_files XML: "
            + ", ".join(missing_stops)
        )

    return manifest


def resolve_active_network_dir(preferred_dir: Path | None = None) -> Path:
    """Resolve the active SUMO network directory, falling back to the synthetic fixture if needed."""
    if preferred_dir is not None:
        target = Path(preferred_dir).resolve()
        load_and_validate_manifest(target)
        return target

    canonical_dir = DEFAULT_CANONICAL_NETWORK_DIR.resolve()
    if (canonical_dir / "manifest.json").is_file():
        try:
            load_and_validate_manifest(canonical_dir)
            return canonical_dir
        except ManifestValidationError as exc:
            logger.warning(
                "Canonical SUMO network at %s failed validation (%s); "
                "falling back to synthetic fixture mode at %s.",
                canonical_dir,
                exc,
                DEFAULT_FIXTURE_NETWORK_DIR,
            )
    else:
        logger.warning(
            "Canonical SUMO manifest not found in %s; falling back to synthetic fixture mode at %s.",
            canonical_dir,
            DEFAULT_FIXTURE_NETWORK_DIR,
        )

    fixture_dir = DEFAULT_FIXTURE_NETWORK_DIR.resolve()
    load_and_validate_manifest(fixture_dir)
    return fixture_dir
