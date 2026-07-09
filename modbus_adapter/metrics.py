"""Per-instance counters and CloudWatch-friendly Modbus operational metrics."""
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from edgecommons.metrics.metric_builder import MetricBuilder

from . import codec


MODBUS_CONNECTION = "ModbusConnection"
MODBUS_INVENTORY = "ModbusInventory"
MODBUS_POLL = "ModbusPoll"
MODBUS_PUBLISH = "ModbusPublish"
MODBUS_COMMAND = "ModbusCommand"

RESULT_SUCCESS = "success"
RESULT_ERROR = "error"

COMMAND_VERBS = ("sb/read", "sb/write", "sb/status", "sb/signals", "reconnect", "repoll")


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


@dataclass
class _Counter:
    total: float = 0.0
    interval: float = 0.0

    def add(self, value=1.0):
        self.total += value
        self.interval += value

    def reset_interval(self):
        self.interval = 0.0


class ModbusOperationalMetrics:
    """Defines and emits Modbus-specific operational metric families for one device instance.

    The dimensions are deliberately low-cardinality: instance, connection type, poll group, table,
    publish mode, command verb, and result. Signal names, addresses, endpoint URLs, and error text are
    never dimensions.
    """

    def __init__(self, metrics, config_manager, config):
        self._metrics = metrics
        self._config = config
        self._lock = threading.Lock()
        self._defined = {}
        self._connection = defaultdict(_Counter)
        self._poll = defaultdict(lambda: defaultdict(_Counter))
        self._publish = defaultdict(_Counter)
        self._command = defaultdict(lambda: defaultdict(_Counter))
        self._last_connected_emit = None

        self._define_connection(config_manager)
        self._define_inventory(config_manager)
        self._define_poll(config_manager)
        self._define_publish(config_manager)
        self._define_command(config_manager)

    def _define_metric(self, name, config_manager, dimensions, measures):
        builder = MetricBuilder.create(name).with_config(config_manager)
        for measure, unit, resolution in measures:
            builder = builder.add_measure(measure, unit, resolution)
        for key, value in dimensions.items():
            builder = builder.add_dimension(key, str(value))
        metric = builder.build()
        try:
            self._metrics.define_metric(metric)
        except Exception:
            pass
        self._defined[(name, tuple(dimensions.items()))] = metric
        return metric

    def _metric(self, name, dimensions):
        return self._defined[(name, tuple(dimensions.items()))]

    def _emit(self, metric, values):
        values = {k: float(v) for k, v in values.items()}
        try:
            target = getattr(self._metrics, "metric_target", None)
            if target is not None:
                target.emit_metric(metric, values)
            else:
                self._metrics.emit_metric(metric.get_name(), values)
        except Exception:
            pass

    def _define_connection(self, config_manager):
        dims = {"instance": self._config.id, "connectionType": self._config.connection.transport}
        self._define_metric(MODBUS_CONNECTION, config_manager, dims, (
            ("connectionState", "Count", 1),
            ("connectAttempts", "Count", 60),
            ("connectFailures", "Count", 60),
            ("reconnectAttempts", "Count", 60),
            ("reconnectFailures", "Count", 60),
            ("connectionDrops", "Count", 60),
            ("connectedDurationMs", "Milliseconds", 60),
        ))

    def _define_inventory(self, config_manager):
        for group, table, _signals, _blocks in self._inventory_rows():
            dims = {"instance": self._config.id, "pollGroup": group.id, "table": table}
            self._define_metric(MODBUS_INVENTORY, config_manager, dims, (
                ("configuredSignals", "Count", 60),
                ("readBlocks", "Count", 60),
                ("configuredPollIntervalMs", "Milliseconds", 60),
                ("coalescingRatio", "None", 60),
                ("writableSignals", "Count", 60),
            ))

    def _define_poll(self, config_manager):
        for group, table, _signals, _blocks in self._inventory_rows():
            for result in (RESULT_SUCCESS, RESULT_ERROR):
                dims = {"instance": self._config.id, "pollGroup": group.id, "table": table, "result": result}
                self._define_metric(MODBUS_POLL, config_manager, dims, (
                    ("pollCycles", "Count", 60),
                    ("pollDurationMs", "Milliseconds", 60),
                    ("protocolReadRequests", "Count", 60),
                    ("protocolReadErrors", "Count", 60),
                    ("registersRead", "Count", 60),
                    ("signalsDecoded", "Count", 60),
                    ("samplesGood", "Count", 60),
                    ("samplesBad", "Count", 60),
                    ("samplesChanged", "Count", 60),
                    ("samplesSuppressed", "Count", 60),
                    ("pollOverruns", "Count", 60),
                ))

    def _define_publish(self, config_manager):
        for publish_mode in sorted({g.publish_mode for g in self._config.poll_groups}):
            dims = {"instance": self._config.id, "publishMode": publish_mode}
            self._define_metric(MODBUS_PUBLISH, config_manager, dims, (
                ("dataMessagesPublished", "Count", 60),
                ("samplesPublished", "Count", 60),
                ("publishFailures", "Count", 60),
                ("batchFlushes", "Count", 60),
                ("batchSize", "Count", 60),
                ("publishLatencyMs", "Milliseconds", 60),
            ))

    def _define_command(self, config_manager):
        for verb in COMMAND_VERBS:
            for result in (RESULT_SUCCESS, RESULT_ERROR):
                dims = {"instance": self._config.id, "verb": verb, "result": result}
                self._define_metric(MODBUS_COMMAND, config_manager, dims, (
                    ("commandRequests", "Count", 60),
                    ("commandLatencyMs", "Milliseconds", 60),
                    ("commandErrors", "Count", 60),
                    ("readSignals", "Count", 60),
                    ("writeSignals", "Count", 60),
                    ("writeFailures", "Count", 60),
                    ("reconnectRequests", "Count", 60),
                    ("repollRequests", "Count", 60),
                ))

    def _inventory_rows(self):
        from .poll_manager import coalesce

        rows = []
        for group in self._config.poll_groups:
            blocks = coalesce(group.signals, group.max_gap)
            tables = sorted({s.table for s in group.signals})
            for table in tables:
                signals = [s for s in group.signals if s.table == table]
                table_blocks = [b for b in blocks if b["table"] == table]
                rows.append((group, table, signals, table_blocks))
        return rows

    def record_connect_attempt(self):
        with self._lock:
            self._connection["connectAttempts"].add()

    def record_connect_failure(self):
        with self._lock:
            self._connection["connectFailures"].add()

    def record_reconnect_attempt(self):
        with self._lock:
            self._connection["reconnectAttempts"].add()

    def record_reconnect_failure(self):
        with self._lock:
            self._connection["reconnectFailures"].add()

    def record_connection_drop(self):
        with self._lock:
            self._connection["connectionDrops"].add()

    def record_poll(self, group_id, table, result, **values):
        key = (group_id, table, result)
        with self._lock:
            self._poll[key]["pollCycles"].add()
            for name, value in values.items():
                self._poll[key][name].add(value)

    def record_poll_overrun(self, group_id, table, result):
        key = (group_id, table, result)
        with self._lock:
            self._poll[key]["pollOverruns"].add()

    def record_publish(self, publish_mode, **values):
        with self._lock:
            for name, value in values.items():
                self._publish[(publish_mode, name)].add(value)

    def record_command(self, verb, result, **values):
        key = (verb, result)
        with self._lock:
            self._command[key]["commandRequests"].add()
            if result == RESULT_ERROR:
                self._command[key]["commandErrors"].add()
            for name, value in values.items():
                self._command[key][name].add(value)

    def emit(self, connected):
        self._emit_connection(connected)
        self._emit_inventory()
        self._emit_poll()
        self._emit_publish()
        self._emit_command()

    def _emit_connection(self, connected):
        now = time.monotonic()
        if connected:
            duration_ms = 0.0 if self._last_connected_emit is None else (now - self._last_connected_emit) * 1000.0
            self._last_connected_emit = now
        else:
            duration_ms = 0.0
            self._last_connected_emit = None
        dims = {"instance": self._config.id, "connectionType": self._config.connection.transport}
        with self._lock:
            values = {
                "connectionState": 1.0 if connected else 0.0,
                "connectAttempts": self._connection["connectAttempts"].interval,
                "connectFailures": self._connection["connectFailures"].interval,
                "reconnectAttempts": self._connection["reconnectAttempts"].interval,
                "reconnectFailures": self._connection["reconnectFailures"].interval,
                "connectionDrops": self._connection["connectionDrops"].interval,
                "connectedDurationMs": duration_ms,
            }
            for counter in self._connection.values():
                counter.reset_interval()
        self._emit(self._metric(MODBUS_CONNECTION, dims), values)

    def _emit_inventory(self):
        for group, table, signals, blocks in self._inventory_rows():
            dims = {"instance": self._config.id, "pollGroup": group.id, "table": table}
            configured = len(signals)
            read_blocks = len(blocks)
            values = {
                "configuredSignals": configured,
                "readBlocks": read_blocks,
                "configuredPollIntervalMs": group.poll_interval_ms,
                "coalescingRatio": (configured / read_blocks) if read_blocks else 0.0,
                "writableSignals": (
                    sum(1 for s in signals if s.table in codec.WRITABLE_TABLES)
                    if self._config.write_enabled else 0
                ),
            }
            self._emit(self._metric(MODBUS_INVENTORY, dims), values)

    def _emit_poll(self):
        rows = []
        with self._lock:
            for key, counters in self._poll.items():
                rows.append((key, {name: counter.interval for name, counter in counters.items()}))
                for counter in counters.values():
                    counter.reset_interval()
        for (group_id, table, result), intervals in rows:
            dims = {"instance": self._config.id, "pollGroup": group_id, "table": table, "result": result}
            values = {name: intervals.get(name, 0.0) for name in (
                "pollCycles", "pollDurationMs", "protocolReadRequests", "protocolReadErrors",
                "registersRead", "signalsDecoded", "samplesGood", "samplesBad", "samplesChanged",
                "samplesSuppressed", "pollOverruns",
            )}
            self._emit(self._metric(MODBUS_POLL, dims), values)

    def _emit_publish(self):
        by_mode = defaultdict(dict)
        with self._lock:
            for (publish_mode, name), counter in self._publish.items():
                by_mode[publish_mode][name] = counter.interval
                counter.reset_interval()
        for publish_mode in {g.publish_mode for g in self._config.poll_groups}:
            dims = {"instance": self._config.id, "publishMode": publish_mode}
            intervals = by_mode[publish_mode]
            values = {name: intervals.get(name, 0.0) for name in (
                "dataMessagesPublished", "samplesPublished", "publishFailures",
                "batchFlushes", "batchSize", "publishLatencyMs",
            )}
            self._emit(self._metric(MODBUS_PUBLISH, dims), values)

    def _emit_command(self):
        rows = []
        with self._lock:
            for key, counters in self._command.items():
                rows.append((key, {name: counter.interval for name, counter in counters.items()}))
                for counter in counters.values():
                    counter.reset_interval()
        for verb in COMMAND_VERBS:
            for result in (RESULT_SUCCESS, RESULT_ERROR):
                dims = {"instance": self._config.id, "verb": verb, "result": result}
                intervals = {}
                for row_key, row_intervals in rows:
                    if row_key == (verb, result):
                        intervals = row_intervals
                        break
                values = {name: intervals.get(name, 0.0) for name in (
                    "commandRequests", "commandLatencyMs", "commandErrors", "readSignals",
                    "writeSignals", "writeFailures", "reconnectRequests", "repollRequests",
                )}
                self._emit(self._metric(MODBUS_COMMAND, dims), values)
