"""Unit tests for the southbound_health metric wiring (HealthMetrics) using a fake metric emitter.

The ``messaging`` metric target auto-routes ``southbound_health`` onto the
reserved ``metric`` class (ecv1/{device}/modbus-adapter/metric/southbound_health) inside the
metric subsystem — the component just defines the metric and emits measures, so this wiring
stays a body ``instance`` dimension."""
from modbus_adapter.health import HealthMetrics
from modbus_adapter.metrics import ClientMetrics


class FakeMetrics:
    def __init__(self, emit_error=None):
        self.defined = []
        self.emitted = []
        self._emit_error = emit_error

    def define_metric(self, metric):
        self.defined.append(metric)

    def emit_metric(self, name, values):
        if self._emit_error:
            raise self._emit_error
        self.emitted.append((name, values))


class FakeCM:
    def get_thing_name(self):
        return "thing1"

    def get_component_name(self):
        return "modbus-adapter"


def test_defines_metric_and_emits_the_exact_section5_measure_set():
    import time

    from modbus_adapter.health import HEALTH_MEASURES

    metrics = FakeMetrics()
    counters = ClientMetrics()
    counters.increment_read_error(4)
    counters.increment_reconnect(2)
    counters.set_poll_latency(12.0)
    counters.set_publish_latency(3.5)
    counters.note_signal_update("u1/holding/0/uint16", time.monotonic() - 100)  # stale (>1s)
    h = HealthMetrics(metrics, FakeCM(), "plc1", counters, stale_signal_secs=1)
    assert len(metrics.defined) == 1
    h.emit(True)
    name, values = metrics.emitted[0]
    assert name == "southbound_health"
    assert set(values) == set(HEALTH_MEASURES)                    # the exact §5 set
    assert values["connectionState"] == 1.0 and values["readErrors"] == 4.0
    assert values["pollLatencyMs"] == 12.0 and values["publishLatencyMs"] == 3.5
    assert values["reconnects"] == 2.0 and values["staleSignals"] == 1.0
    # interval counters reset on emit; the connection gauge follows the argument
    h.emit(False)
    second = metrics.emitted[1][1]
    assert second["connectionState"] == 0.0 and second["readErrors"] == 0.0
    assert second["reconnects"] == 0.0


def test_emit_never_raises():
    h = HealthMetrics(FakeMetrics(emit_error=RuntimeError("x")), FakeCM(), "plc1", ClientMetrics())
    h.emit(True)   # must be swallowed
