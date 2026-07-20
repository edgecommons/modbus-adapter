"""The canonical ``southbound_health`` metric (SOUTHBOUND.md §5), per instance.

Emits the exact §5 measure set — ``connectionState``, ``publishLatencyMs``, ``pollLatencyMs``,
``readErrors``, ``staleSignals`` — plus the §5-optional ``reconnects``. The gauge/latency values and
the staleness count are read from the shared :class:`~modbus_adapter.metrics.ClientMetrics` the poll
manager, publisher, and device tick feed; ``staleSignals`` counts configured signals with no update
for longer than ``component.global.healthThresholds.staleSignalSecs`` (default 30).

:data:`HEALTH_MEASURES` is the parity anchor a test asserts against — keep it, the builder below, and
``docs/reference/metrics.md`` in step.
"""
import logging
import time

from edgecommons.metrics.metric_builder import MetricBuilder

from .config.server_configuration import DEFAULT_STALE_SIGNAL_SECS

LOGGER = logging.getLogger("modbus_adapter.health")

METRIC = "southbound_health"

#: The exact SOUTHBOUND.md §5 measure set (+ the §5-optional ``reconnects``).
HEALTH_MEASURES = (
    "connectionState", "publishLatencyMs", "pollLatencyMs", "readErrors", "staleSignals",
    "reconnects",
)


class HealthMetrics:
    def __init__(self, metrics, config_manager, instance_id, counters,
                 stale_signal_secs=DEFAULT_STALE_SIGNAL_SECS):
        self._metrics = metrics                 # the MetricEmitter (gg.get_metrics())
        self._counters = counters
        self._stale_after = max(1, int(stale_signal_secs))
        metric = (
            MetricBuilder.create(METRIC)
            .with_config(config_manager)
            .add_measure("connectionState", "Count", 1)
            .add_measure("publishLatencyMs", "Milliseconds", 1)
            .add_measure("pollLatencyMs", "Milliseconds", 1)
            .add_measure("readErrors", "Count", 60)
            .add_measure("staleSignals", "Count", 60)
            .add_measure("reconnects", "Count", 60)
            .add_dimension("instance", instance_id)
            .build()
        )
        self._metrics.define_metric(metric)

    def emit(self, connected: bool):
        try:
            now = time.monotonic()
            self._metrics.emit_metric(METRIC, {
                "connectionState": 1.0 if connected else 0.0,
                "publishLatencyMs": float(self._counters.publish_latency_ms()),
                "pollLatencyMs": float(self._counters.poll_latency_ms()),
                "readErrors": float(self._counters.take_interval_read_errors()),
                "staleSignals": float(self._counters.stale_count(now, self._stale_after)),
                "reconnects": float(self._counters.take_interval_reconnects()),
            })
        except Exception as e:  # noqa: BLE001 - health must never crash the device
            LOGGER.debug("health emit failed: %s", e)
