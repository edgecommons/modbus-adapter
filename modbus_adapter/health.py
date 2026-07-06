"""The standard ``southbound_health`` metric (contract §5), per instance."""
import logging

from edgecommons.metrics.metric_builder import MetricBuilder

LOGGER = logging.getLogger("modbus_adapter.health")

METRIC = "southbound_health"


class HealthMetrics:
    def __init__(self, metrics, config_manager, instance_id, counters):
        self._metrics = metrics                 # the MetricEmitter (gg.get_metrics())
        self._counters = counters
        metric = (
            MetricBuilder.create(METRIC)
            .with_config(config_manager)
            .add_measure("connectionState", "Count", 1)
            .add_measure("readErrors", "Count", 60)
            .add_dimension("instance", instance_id)
            .build()
        )
        self._metrics.define_metric(metric)

    def emit(self, connected: bool):
        try:
            self._metrics.emit_metric(METRIC, {
                "connectionState": 1.0 if connected else 0.0,
                "readErrors": float(self._counters.take_interval_read_errors()),
            })
        except Exception as e:  # noqa: BLE001 - health must never crash the device
            LOGGER.debug("health emit failed: %s", e)
