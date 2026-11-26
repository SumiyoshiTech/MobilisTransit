"""Shadow-safe Eclipse SUMO binary discovery and SUMO_HOME initialization."""

from __future__ import annotations

import importlib.metadata
import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class SumoNotFoundError(RuntimeError):
    """Raised when an Eclipse SUMO executable cannot be located."""


def _binary_candidates(gui: bool = False) -> list[str]:
    stem = "sumo-gui" if gui else "sumo"
    if sys.platform == "win32":
        return [f"{stem}.exe", stem]
    return [stem]


def _resolve_from_sumo_home(sumo_home: str, candidates: list[str]) -> Path | None:
    home_path = Path(sumo_home).expanduser()
    for candidate in candidates:
        binary = home_path / "bin" / candidate
        if binary.is_file():
            return binary.resolve()
    return None


def _resolve_from_path(candidates: list[str]) -> Path | None:
    for candidate in candidates:
        located = shutil.which(candidate)
        if located:
            binary = Path(located)
            if binary.is_file():
                return binary.resolve()
    return None


def _resolve_from_eclipse_sumo_metadata(candidates: list[str]) -> Path | None:
    """Locate SUMO binaries from PyPI `eclipse-sumo` metadata without importing `sumo`."""
    try:
        dist = importlib.metadata.distribution("eclipse-sumo")
    except importlib.metadata.PackageNotFoundError:
        return None

    target_suffixes = {("sumo", "bin", candidate.lower()) for candidate in candidates}
    for dist_file in dist.files or ():
        parts = dist_file.parts
        if len(parts) >= 3 and tuple(p.lower() for p in parts[-3:]) in target_suffixes:
            located = Path(dist.locate_file(dist_file))
            if located.is_file():
                return located.resolve()

    for candidate in candidates:
        located = Path(dist.locate_file(f"sumo/bin/{candidate}"))
        if located.is_file():
            return located.resolve()

    return None


def resolve_sumo_binary(gui: bool = False) -> Path:
    """Locate the SUMO executable in strict priority order without shadowing `eclipse-sumo`.

    Resolution order:
    1. ``os.environ["SUMO_HOME"]/bin/sumo(.exe)`` (or ``sumo-gui(.exe)``).
    2. ``shutil.which("sumo")`` (or ``sumo-gui``).
    3. ``importlib.metadata.distribution("eclipse-sumo")`` site-packages binary.
    """
    candidates = _binary_candidates(gui=gui)

    env_home = os.environ.get("SUMO_HOME")
    if env_home:
        binary = _resolve_from_sumo_home(env_home, candidates)
        if binary is not None:
            return binary
        logger.warning(
            "SUMO_HOME=%r does not contain %s under bin/; falling back to PATH and eclipse-sumo.",
            env_home,
            candidates[0],
        )

    binary = _resolve_from_path(candidates)
    if binary is not None:
        return binary

    binary = _resolve_from_eclipse_sumo_metadata(candidates)
    if binary is not None:
        return binary

    binary_name = "sumo-gui" if gui else "sumo"
    raise SumoNotFoundError(
        f"Could not locate Eclipse SUMO executable '{binary_name}'. "
        "Install Eclipse SUMO and set SUMO_HOME (e.g. C:\\Program Files (x86)\\SUMO), "
        "add its bin/ directory to PATH, or install the Python wheel via "
        "`pip install eclipse-sumo` inside the project virtual environment."
    )


def ensure_sumo_home_env() -> Path:
    """Ensure ``SUMO_HOME`` is set to a valid SUMO root directory in the current process."""
    candidates = _binary_candidates(gui=False)
    env_home = os.environ.get("SUMO_HOME")
    if env_home:
        binary = _resolve_from_sumo_home(env_home, candidates)
        if binary is not None:
            resolved_home = Path(env_home).expanduser().resolve()
            os.environ["SUMO_HOME"] = str(resolved_home)
            return resolved_home

    binary = resolve_sumo_binary(gui=False)
    sumo_home = binary.parent.parent if binary.parent.name.lower() == "bin" else binary.parent
    resolved_home = sumo_home.resolve()
    os.environ["SUMO_HOME"] = str(resolved_home)
    return resolved_home
