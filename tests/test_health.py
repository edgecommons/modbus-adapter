"""Unit tests for the southbound_health metric wiring (HealthMetrics) using a fake metric emitter.

Since the UNS change, the ``messaging`` metric target auto-routes ``southbound_health`` onto the
reserved ``metric`` class (ecv1/{device}/ModbusAdapter/main/metric/southbound_health) inside the
metric subsystem — the component still just defines the metric and emits measures, so this wiring is
unchanged and stays a body ``instance`` dimension."""
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
        return "ModbusAdapter"


def test_defines_metric_and_emits_measures():
    metrics = FakeMetrics()
    counters = ClientMetrics()
    counters.increment_read_error(4)
    h = HealthMetrics(metrics, FakeCM(), "plc1", counters)
    assert len(metrics.defined) == 1
    h.emit(True)
    name, values = metrics.emitted[0]
    assert name == "southbound_health"
    assert values["connectionState"] == 1.0 and values["readErrors"] == 4.0
    h.emit(False)
    assert metrics.emitted[1][1]["connectionState"] == 0.0


def test_emit_never_raises():
    h = HealthMetrics(FakeMetrics(emit_error=RuntimeError("x")), FakeCM(), "plc1", ClientMetrics())
    h.emit(True)   # must be swallowed
