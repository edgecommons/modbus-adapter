"""Unit tests for the UNS data publisher, the evt emitter, the client counters, and the poll manager's
on-demand poll path — all with in-memory fakes."""
from modbus_adapter.events import EventEmitter
from modbus_adapter.metrics import ClientMetrics
from modbus_adapter.poll_manager import PollManager
from modbus_adapter.publisher import SignalUpdatePublisher
from tests._fakes import FakeConn, FakeInstance, FakeMessaging, make_config


# --- SignalUpdatePublisher ------------------------------------------------------------------
def _pub(batch_ms=0):
    config = make_config(batch_ms=batch_ms)
    msg = FakeMessaging()
    pub = SignalUpdatePublisher(msg, FakeInstance(), config)
    group = config.poll_groups[0]
    signal = group.signals[0]                       # Counter16, holding 0, uint16
    return pub, msg, group, signal


def test_publish_mints_uns_data_topic_and_body():
    pub, msg, group, signal = _pub()
    pub.offer(group, signal, SignalUpdatePublisher.make_sample(1234))
    assert len(msg.published) == 1
    topic, envelope = msg.published[0]
    assert topic == "ecv1/thing1/ModbusAdapter/plc1/data/Counter16"
    body = envelope["body"]
    assert body["device"]["adapter"] == "modbus" and body["device"]["instance"] == "plc1"
    assert body["signal"]["name"] == "Counter16" and body["signal"]["id"] == "u1/holding/0/uint16"
    assert body["signal"]["address"]["table"] == "holding"
    assert body["samples"][0]["value"] == 1234 and body["samples"][0]["quality"] == "GOOD"


def test_publish_batches_until_flush():
    pub, msg, group, signal = _pub(batch_ms=100)
    pub.offer(group, signal, SignalUpdatePublisher.make_sample(1))
    pub.offer(group, signal, SignalUpdatePublisher.make_sample(2))
    assert msg.published == []                       # buffered, not sent
    pub.flush()
    assert len(msg.published) == 1
    assert [s["value"] for s in msg.published[0][1]["body"]["samples"]] == [1, 2]


def test_publish_swallows_broker_error():
    config = make_config()
    class Boom:
        def publish(self, *a, **k):
            raise RuntimeError("broker down")
    pub = SignalUpdatePublisher(Boom(), FakeInstance(), config)
    g = config.poll_groups[0]
    pub.offer(g, g.signals[0], SignalUpdatePublisher.make_sample(1))   # must not raise


# --- EventEmitter ---------------------------------------------------------------------------
def test_event_emit_on_evt_class():
    msg = FakeMessaging()
    EventEmitter(msg, FakeInstance()).emit("connection", {"instance": "plc1", "connected": True})
    assert len(msg.published) == 1
    topic, envelope = msg.published[0]
    assert topic == "ecv1/thing1/ModbusAdapter/plc1/evt/connection"
    assert envelope["body"]["connected"] is True


def test_event_emit_never_raises():
    class Boom:
        def publish(self, *a, **k):
            raise RuntimeError("x")
    EventEmitter(Boom(), FakeInstance()).emit("write", {"ok": False})   # must not raise


# --- ClientMetrics --------------------------------------------------------------------------
def test_client_metrics_counters():
    m = ClientMetrics()
    m.increment_read(3)
    m.increment_write()
    m.increment_read_error(2)
    assert m.take_interval_read_errors() == 2
    assert m.take_interval_read_errors() == 0        # reset on read
    d = m.to_dict()
    assert d["read"]["total"] == 3 and d["write"]["total"] == 1
    assert m.to_dict()["read"]["interval"] == 0      # interval reset by to_dict


# --- PollManager on-demand path -------------------------------------------------------------
def test_poll_once_reads_and_publishes():
    config = make_config(signals=[
        {"name": "Counter16", "table": "holding", "address": 0, "type": "uint16"},
        {"name": "Next", "table": "holding", "address": 1, "type": "uint16"},
    ])
    conn = FakeConn()
    conn.holding[0] = 11
    conn.holding[1] = 22
    msg = FakeMessaging()
    pub = SignalUpdatePublisher(msg, FakeInstance(), config)
    counters = ClientMetrics()
    pm = PollManager(conn, config, pub, counters)
    # poll_once lazily coalesces + reads each group synchronously (no poll threads started)
    polled = pm.poll_once()
    assert polled == 1
    published_names = {t.rsplit("/", 1)[1] for t, _ in msg.published}
    assert {"Counter16", "Next"} <= published_names


def test_resolved_signals_shape():
    config = make_config()
    pm = PollManager(FakeConn(), config, None, ClientMetrics())
    names = {s["name"] for s in pm.resolved_signals()}
    assert "Counter16" in names and all("signalId" in s and "address" in s for s in pm.resolved_signals())


def test_poll_once_block_read_error_marks_bad():
    config = make_config(signals=[{"name": "Counter16", "table": "holding", "address": 0, "type": "uint16"}])
    conn = FakeConn()

    def boom(*a, **k):
        raise RuntimeError("read timeout")
    conn.read = boom
    msg = FakeMessaging()
    counters = ClientMetrics()
    pm = PollManager(conn, config, SignalUpdatePublisher(msg, FakeInstance(), config), counters)
    pm.poll_once()
    # a failed block read publishes BAD samples for its signals
    assert msg.published and msg.published[0][1]["body"]["samples"][0]["quality"] == "BAD"


def test_start_and_stop_run_poll_threads():
    import time
    config = make_config(signals=[{"name": "Counter16", "table": "holding", "address": 0, "type": "uint16"}])
    conn = FakeConn()
    conn.holding[0] = 5
    msg = FakeMessaging()
    pm = PollManager(conn, config, SignalUpdatePublisher(msg, FakeInstance(), config), ClientMetrics())
    pm.start()
    time.sleep(0.2)
    pm.stop()
    assert any(t.endswith("/data/Counter16") for t, _ in msg.published)
