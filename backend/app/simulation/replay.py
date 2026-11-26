"""Recorded-journey replay stream controller supporting Play, Pause, Step, and Restart.

Preserves source-clock freezing while paused (BR-003) and scoped monotonic
sequence/execution reset semantics on restart (BR-009).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from uuid import uuid4

from backend.app.adapter.source_adapter import (
    CanonicalVehicleState,
    PlaybackState,
    ReplaySourceAdapter,
)


def _new_execution_id() -> str:
    return f"replay-{uuid4().hex[:12]}"


def _now_ms() -> int:
    return int(time.time() * 1000)


class RecordedJourneyPlayer:
    """Thread-safe controller replaying recorded `CanonicalVehicleState` snapshots."""

    def __init__(
        self,
        snapshots: Sequence[CanonicalVehicleState],
        *,
        execution_id: str | None = None,
        initial_state: PlaybackState = "PAUSED",
        adapter: ReplaySourceAdapter | None = None,
    ) -> None:
        if not snapshots:
            raise ValueError("RecordedJourneyPlayer requires at least one snapshot.")

        self._lock = threading.RLock()
        self._adapter = adapter or ReplaySourceAdapter()
        self._snapshots: tuple[CanonicalVehicleState, ...] = tuple(
            CanonicalVehicleState.model_validate(item.model_dump()) for item in snapshots
        )
        self._cursor: int = 0
        self._execution_id: str = (
            execution_id.strip()
            if isinstance(execution_id, str) and execution_id.strip()
            else _new_execution_id()
        )
        self._sequence_number: int = 1
        self._playback_state: PlaybackState = initial_state
        self._emitted_at_ms: int = _now_ms()

    @property
    def execution_id(self) -> str:
        with self._lock:
            return self._execution_id

    @property
    def sequence_number(self) -> int:
        with self._lock:
            return self._sequence_number

    @property
    def playback_state(self) -> PlaybackState:
        with self._lock:
            return self._playback_state

    @property
    def current_index(self) -> int:
        with self._lock:
            return self._cursor

    @property
    def total_snapshots(self) -> int:
        return len(self._snapshots)

    @property
    def is_finished(self) -> bool:
        with self._lock:
            return self._cursor >= len(self._snapshots) - 1

    def _build_current_state_unlocked(self) -> CanonicalVehicleState:
        return self._adapter.adapt(
            self._snapshots[self._cursor],
            playback_state=self._playback_state,
            execution_id=self._execution_id,
            sequence_number=self._sequence_number,
            worker_generated_at_ms=self._emitted_at_ms,
        )

    def current_state(self) -> CanonicalVehicleState:
        """Return the current canonical snapshot without advancing `simulation_clock_s`."""
        with self._lock:
            return self._build_current_state_unlocked()

    def play(self) -> CanonicalVehicleState:
        """Transition playback to `PLAYING` (or remain `STOPPED` if already at end)."""
        with self._lock:
            if self._cursor >= len(self._snapshots) - 1:
                self._playback_state = "STOPPED"
            else:
                self._playback_state = "PLAYING"
            self._emitted_at_ms = _now_ms()
            return self._build_current_state_unlocked()

    def pause(self) -> CanonicalVehicleState:
        """Pause playback, freezing `simulation_clock_s` (BR-003)."""
        with self._lock:
            self._playback_state = "PAUSED"
            self._emitted_at_ms = _now_ms()
            return self._build_current_state_unlocked()

    def tick(self) -> CanonicalVehicleState:
        """Advance one snapshot if `PLAYING`; otherwise remain frozen on the current clock."""
        with self._lock:
            if self._playback_state != "PLAYING":
                return self._build_current_state_unlocked()

            if self._cursor < len(self._snapshots) - 1:
                self._cursor += 1
                self._sequence_number += 1
                self._emitted_at_ms = _now_ms()
                if self._cursor >= len(self._snapshots) - 1:
                    self._playback_state = "STOPPED"
            else:
                self._playback_state = "STOPPED"

            return self._build_current_state_unlocked()

    def step(self, steps: int = 1) -> CanonicalVehicleState:
        """Explicitly advance by `steps` snapshots, incrementing `sequence_number`."""
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
            raise ValueError("steps must be a positive integer.")

        with self._lock:
            if self._cursor < len(self._snapshots) - 1:
                self._cursor = min(len(self._snapshots) - 1, self._cursor + steps)
                self._sequence_number += 1
                self._emitted_at_ms = _now_ms()
                if self._cursor >= len(self._snapshots) - 1:
                    self._playback_state = "STOPPED"
            else:
                self._playback_state = "STOPPED"
            return self._build_current_state_unlocked()

    def restart(self, *, auto_play: bool = True) -> CanonicalVehicleState:
        """Reset playback to index 0 with a new `execution_id` and `sequence_number=1` (BR-009)."""
        with self._lock:
            previous_id = self._execution_id
            new_id = _new_execution_id()
            while new_id == previous_id:
                new_id = _new_execution_id()

            self._execution_id = new_id
            self._cursor = 0
            self._sequence_number = 1
            self._playback_state = "PLAYING" if auto_play else "PAUSED"
            self._emitted_at_ms = _now_ms()
            return self._build_current_state_unlocked()
