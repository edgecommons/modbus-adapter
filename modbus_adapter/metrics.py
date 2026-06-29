"""Lightweight per-instance read/write/error counters (interval + lifetime), for the status query
and the health metric. Thread-safe (poll threads + command handlers touch it)."""
import threading


class ClientMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._read_interval = 0
        self._read_total = 0
        self._write_interval = 0
        self._write_total = 0
        self._read_errors_interval = 0

    def increment_read(self, n=1):
        with self._lock:
            self._read_interval += n
            self._read_total += n

    def increment_write(self, n=1):
        with self._lock:
            self._write_interval += n
            self._write_total += n

    def increment_read_error(self, n=1):
        with self._lock:
            self._read_errors_interval += n

    def take_interval_read_errors(self) -> int:
        with self._lock:
            v = self._read_errors_interval
            self._read_errors_interval = 0
            return v

    def to_dict(self) -> dict:
        with self._lock:
            d = {
                "read": {"interval": self._read_interval, "total": self._read_total},
                "write": {"interval": self._write_interval, "total": self._write_total},
            }
            self._read_interval = 0
            self._write_interval = 0
            return d
