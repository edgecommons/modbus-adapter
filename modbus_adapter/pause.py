"""Per-instance pause state (SOUTHBOUND.md §2.2 ``sb/pause``/``sb/resume``).

A thread-safe latch shared by one device's poll manager (which suspends polling + publishing while
set), the device tick (which suspends the batched-publish flush), and the command surface (which
toggles it and refuses ``repoll`` while it is set). Pausing an already-paused instance — or resuming a
running one — is not an error: :meth:`PauseState.set` reports whether the state actually *changed* so
the confirmed reply can carry ``{paused, changed}`` idempotently.
"""
import threading


class PauseState:
    def __init__(self, paused: bool = False):
        self._lock = threading.Lock()
        self._paused = paused

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def set(self, paused: bool) -> bool:
        """Set the paused flag, returning whether it actually changed (idempotent)."""
        with self._lock:
            changed = self._paused != paused
            self._paused = paused
            return changed
