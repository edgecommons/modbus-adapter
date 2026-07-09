"""Focused tests for the Modbus operational metric families."""

from modbus_adapter.metrics import (
    MODBUS_COMMAND,
    MODBUS_CONNECTION,
    MODBUS_INVENTORY,
    MODBUS_POLL,
    MODBUS_PUBLISH,
    ModbusOperationalMetrics,
    RESULT_ERROR,
    RESULT_SUCCESS,
)
from tests._fakes import make_config


class RecordingTarget:
    def __init__(self):
        self.emitted = []

    def emit_metric(self, metric, values):
        self.emitted.append((metric, values))


class FakeMetrics:
    def __init__(self):
        self.defined = []
        self.metric_target = RecordingTarget()

    def define_metric(self, metric):
        self.defined.append(metric)

    def emit_metric(self, name, values):
        self.metric_target.emitted.append((name, values))


class FakeCM:
    def get_thing_name(self):
        return "thing1"

    def get_component_name(self):
        return "modbus-adapter"


def _metric_dims(metric):
    return metric.get_dimensions()


def test_operational_metrics_define_requested_low_cardinality_families():
    metrics = FakeMetrics()
    ModbusOperationalMetrics(metrics, FakeCM(), make_config())

    names = {m.get_name() for m in metrics.defined}
    assert {MODBUS_CONNECTION, MODBUS_INVENTORY, MODBUS_POLL, MODBUS_PUBLISH, MODBUS_COMMAND} <= names

    for metric in metrics.defined:
        dims = _metric_dims(metric)
        assert "instance" in dims
        assert "endpoint" not in dims
        assert "signal" not in dims
        assert "address" not in dims
        assert "error" not in dims
        assert len(dims) <= 10

    poll_dims = [_metric_dims(m) for m in metrics.defined if m.get_name() == MODBUS_POLL]
    assert {"pollGroup", "table", "result"} <= set(poll_dims[0])
    assert {d["result"] for d in poll_dims} == {"success", "error"}


def test_operational_metrics_emit_interval_values_with_static_dimensions():
    metrics = FakeMetrics()
    op = ModbusOperationalMetrics(metrics, FakeCM(), make_config())

    op.record_connect_attempt()
    op.record_connect_failure()
    op.record_poll(
        "g",
        "holding",
        RESULT_SUCCESS,
        pollDurationMs=12.5,
        protocolReadRequests=2,
        registersRead=8,
        signalsDecoded=3,
        samplesGood=3,
        samplesChanged=2,
        samplesSuppressed=1,
    )
    op.record_publish(
        "onChange",
        dataMessagesPublished=2,
        samplesPublished=3,
        batchFlushes=1,
        batchSize=3,
        publishLatencyMs=4.0,
    )
    op.record_command("sb/read", RESULT_SUCCESS, commandLatencyMs=7.0, readSignals=3)

    op.emit(True)

    emitted = [
        (metric.get_name(), metric.get_dimensions(), values)
        for metric, values in metrics.metric_target.emitted
    ]
    connection = next(e for e in emitted if e[0] == MODBUS_CONNECTION)
    assert connection[1]["connectionType"] == "tcp"
    assert connection[2]["connectionState"] == 1.0
    assert connection[2]["connectAttempts"] == 1.0
    assert connection[2]["connectFailures"] == 1.0

    poll = next(
        e for e in emitted
        if e[0] == MODBUS_POLL and e[1]["pollGroup"] == "g"
        and e[1]["table"] == "holding" and e[1]["result"] == "success"
    )
    assert poll[2]["pollCycles"] == 1.0
    assert poll[2]["protocolReadRequests"] == 2.0
    assert poll[2]["samplesChanged"] == 2.0
    assert poll[2]["samplesSuppressed"] == 1.0

    publish = next(e for e in emitted if e[0] == MODBUS_PUBLISH and e[1]["publishMode"] == "onChange")
    assert publish[2]["dataMessagesPublished"] == 2.0
    assert publish[2]["samplesPublished"] == 3.0
    assert publish[2]["batchFlushes"] == 1.0

    command = next(
        e for e in emitted
        if e[0] == MODBUS_COMMAND and e[1]["verb"] == "sb/read" and e[1]["result"] == "success"
    )
    assert command[2]["commandRequests"] == 1.0
    assert command[2]["readSignals"] == 3.0

    metrics.metric_target.emitted = []
    op.emit(True)
    second_connection = next(
        (metric, values) for metric, values in metrics.metric_target.emitted
        if metric.get_name() == MODBUS_CONNECTION
    )
    assert second_connection[1]["connectAttempts"] == 0.0
    assert second_connection[1]["connectFailures"] == 0.0


def test_inventory_metrics_report_poll_shape_per_group_and_table():
    metrics = FakeMetrics()
    op = ModbusOperationalMetrics(metrics, FakeCM(), make_config())

    op.emit(False)

    inventory = [
        (metric.get_dimensions(), values)
        for metric, values in metrics.metric_target.emitted
        if metric.get_name() == MODBUS_INVENTORY
    ]
    holding = next(row for row in inventory if row[0]["pollGroup"] == "g" and row[0]["table"] == "holding")
    assert holding[1]["configuredSignals"] == 2.0
    assert holding[1]["readBlocks"] == 2.0
    assert holding[1]["configuredPollIntervalMs"] == 500.0
    assert holding[1]["coalescingRatio"] == 1.0
    assert holding[1]["writableSignals"] == 2.0


def test_inventory_writable_signals_respects_write_enablement():
    metrics = FakeMetrics()
    op = ModbusOperationalMetrics(metrics, FakeCM(), make_config(write_enabled=False))

    op.emit(False)

    inventory = [
        (metric.get_dimensions(), values)
        for metric, values in metrics.metric_target.emitted
        if metric.get_name() == MODBUS_INVENTORY
    ]
    holding = next(row for row in inventory if row[0]["pollGroup"] == "g" and row[0]["table"] == "holding")
    assert holding[1]["writableSignals"] == 0.0


def test_poll_overrun_uses_actual_result_dimension():
    metrics = FakeMetrics()
    op = ModbusOperationalMetrics(metrics, FakeCM(), make_config())

    op.record_poll("g", "holding", RESULT_ERROR, protocolReadErrors=1)
    op.record_poll_overrun("g", "holding", RESULT_ERROR)
    op.emit(False)

    emitted = [
        (metric.get_name(), metric.get_dimensions(), values)
        for metric, values in metrics.metric_target.emitted
    ]
    error_poll = next(
        e for e in emitted
        if e[0] == MODBUS_POLL and e[1]["pollGroup"] == "g"
        and e[1]["table"] == "holding" and e[1]["result"] == "error"
    )
    assert error_poll[2]["pollOverruns"] == 1.0
    assert not [
        e for e in emitted
        if e[0] == MODBUS_POLL and e[1]["pollGroup"] == "g"
        and e[1]["table"] == "holding" and e[1]["result"] == "success"
        and e[2]["pollOverruns"] > 0.0
    ]
