"""Unit tests for the UNS data publisher (through the real ``data()`` facade), the evt emitter
(through the real ``events()`` facade), the client counters, and the poll manager's on-demand poll
path -- all with in-memory fakes (a recording messaging client bound to a real ``EdgeCommonsInstance``, so
these tests pin the real facade body/topic contract, not a hand-rolled shape)."""
from edgecommons.facades.quality import Quality

from modbus_adapter.events import EventEmitter
from modbus_adapter.metrics import ClientMetrics
from modbus_adapter.poll_manager import PollManager
from modbus_adapter.publisher import SignalUpdatePublisher
from tests._fakes import FakeConn, FakeInstance, FakeMessaging, make_config


# --- SignalUpdatePublisher (through data()) -------------------------------------------------
def _pub(batch_ms=0):
    config = make_config(batch_ms=batch_ms)
    msg = FakeMessaging()
    pub = SignalUpdatePublisher(FakeInstance(msg).data(), config)
    group = config.poll_groups[0]
    signal = group.signals[0]                       # Counter16, holding 0, uint16
    return pub, msg, group, signal


def test_publish_mints_uns_data_topic_and_body():
    pub, msg, group, signal = _pub()
    pub.offer(group, signal, SignalUpdatePublisher.make_sample(1234))
    assert len(msg.published) == 1
    topic, envelope = msg.published[0]
    assert topic == "ecv1/thing1/modbus-adapter/plc1/data/Counter16"
    body = envelope.body
    assert body["device"]["adapter"] == "modbus" and body["device"]["instance"] == "plc1"
    assert body["signal"]["name"] == "Counter16" and body["signal"]["id"] == "u1/holding/0/uint16"
    assert body["signal"]["address"]["table"] == "holding"
    assert body["samples"][0]["value"] == 1234 and body["samples"][0]["quality"] == "GOOD"
    # Modbus has no native quality notion -- an omitted quality is defaulted by the facade, and
    # marked with the "unspecified" marker so a consumer can tell a synthesized GOOD from a
    # device-reported one (DESIGN-class-facades §2.1, D2).
    assert body["samples"][0]["qualityRaw"] == "unspecified"
    assert "sourceTs" not in body["samples"][0]     # never synthesized -- omitted, not null
    assert body["samples"][0]["serverTs"]


def test_publish_batches_until_flush():
    pub, msg, group, signal = _pub(batch_ms=100)
    pub.offer(group, signal, SignalUpdatePublisher.make_sample(1))
    pub.offer(group, signal, SignalUpdatePublisher.make_sample(2))
    assert msg.published == []                       # buffered, not sent
    pub.flush()
    assert len(msg.published) == 1
    assert [s["value"] for s in msg.published[0][1].body["samples"]] == [1, 2]


def test_publish_swallows_broker_error():
    config = make_config()

    class Boom:
        def publish(self, *a, **k):
            raise RuntimeError("broker down")

        def publish_northbound(self, *a, **k):
            raise RuntimeError("broker down")

    pub = SignalUpdatePublisher(FakeInstance(Boom()).data(), config)
    g = config.poll_groups[0]
    pub.offer(g, g.signals[0], SignalUpdatePublisher.make_sample(1))   # must not raise


def test_publish_value_less_bad_read_uses_the_raw_escape_hatch():
    # A fully failed block read has no value at all -- the data() facade's samples[]
    # structurally requires one (DESIGN-class-facades §5.2, D2), so this goes through the raw
    # escape hatch (publish_body, D5) instead of the normal builder; the topic/identity are
    # still minted by the same instance's data() facade.
    pub, msg, group, signal = _pub()
    pub.offer(group, signal, SignalUpdatePublisher.make_sample(None, Quality.BAD, "read timeout"))
    assert len(msg.published) == 1
    topic, envelope = msg.published[0]
    assert topic == "ecv1/thing1/modbus-adapter/plc1/data/Counter16"
    sample = envelope.body["samples"][0]
    assert sample["value"] is None
    assert sample["quality"] == "BAD" and sample["qualityRaw"] == "read timeout"
    assert sample["sourceTs"] is None
    assert sample["serverTs"]


def test_publish_value_less_sample_with_no_explicit_quality_still_defaults_to_good():
    # Defensive parity with DataFacade.build_body's own defaulting rule, in case a future
    # caller offers a value-less sample without an explicit quality.
    pub, msg, group, signal = _pub()
    pub.offer(group, signal, SignalUpdatePublisher.make_sample(None))
    sample = msg.published[0][1].body["samples"][0]
    assert sample["quality"] == "GOOD" and sample["qualityRaw"] == "unspecified"


def test_publish_mixed_batch_splits_valued_and_valueless_samples():
    # A batchMs window can straddle a transient failure: one tick reads fine, the next times
    # out. Since a value-less sample can't ride the same builder-constructed message as a
    # valued one, the flush emits two messages -- one per shape.
    pub, msg, group, signal = _pub(batch_ms=100)
    pub.offer(group, signal, SignalUpdatePublisher.make_sample(7))
    pub.offer(group, signal, SignalUpdatePublisher.make_sample(None, Quality.BAD, "timeout"))
    pub.flush()
    assert len(msg.published) == 2
    qualities = {envelope.body["samples"][0]["quality"] for _, envelope in msg.published}
    assert qualities == {"GOOD", "BAD"}


# --- EventEmitter (through events()) ---------------------------------------------------------
def test_connection_lost_raises_critical_alarm():
    msg = FakeMessaging()
    EventEmitter(FakeInstance(msg).events()).connection(False, {"endpoint": "tcp://10.0.0.1:502 unit=1"})
    assert len(msg.published) == 1
    topic, envelope = msg.published[0]
    # severity DERIVES the channel: evt/{severity}/{type} -- identical in shape to the OPC UA
    # adapter's evt convention (the drift DESIGN-class-facades §1.2 documents is fixed).
    assert topic == "ecv1/thing1/modbus-adapter/plc1/evt/critical/connection"
    body = envelope.body
    assert body["severity"] == "critical" and body["type"] == "connection"
    assert body["alarm"] is True and body["active"] is True
    assert body["context"]["endpoint"] == "tcp://10.0.0.1:502 unit=1"


def test_connection_restored_clears_the_same_alarm_channel():
    msg = FakeMessaging()
    events = EventEmitter(FakeInstance(msg).events())
    events.connection(False, {"endpoint": "tcp://10.0.0.1:502 unit=1"})
    events.connection(True, {"endpoint": "tcp://10.0.0.1:502 unit=1"})
    assert len(msg.published) == 2
    lost_topic, _ = msg.published[0]
    restored_topic, restored_envelope = msg.published[1]
    assert restored_topic == lost_topic == "ecv1/thing1/modbus-adapter/plc1/evt/critical/connection"
    assert restored_envelope.body["alarm"] is True and restored_envelope.body["active"] is False


def test_write_success_emits_info_severity():
    msg = FakeMessaging()
    EventEmitter(FakeInstance(msg).events()).write(True, "RWInt16", 42)
    topic, envelope = msg.published[0]
    assert topic == "ecv1/thing1/modbus-adapter/plc1/evt/info/write"
    body = envelope.body
    assert body["severity"] == "info" and body["type"] == "write"
    assert body["context"] == {"signal": "RWInt16", "value": 42}


def test_write_failure_emits_warning_severity():
    msg = FakeMessaging()
    EventEmitter(FakeInstance(msg).events()).write(False, "RWInt16", 42, "timeout")
    topic, envelope = msg.published[0]
    assert topic == "ecv1/thing1/modbus-adapter/plc1/evt/warning/write"
    body = envelope.body
    assert body["severity"] == "warning"
    assert body["context"]["error"] == "timeout"


def test_event_emit_never_raises():
    class Boom:
        def publish(self, *a, **k):
            raise RuntimeError("x")

        def publish_northbound(self, *a, **k):
            raise RuntimeError("x")

    EventEmitter(FakeInstance(Boom()).events()).write(False, "sig", 1, "x")   # must not raise
    EventEmitter(FakeInstance(Boom()).events()).connection(False, {})        # must not raise


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
    pub = SignalUpdatePublisher(FakeInstance(msg).data(), config)
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
    pm = PollManager(conn, config, SignalUpdatePublisher(FakeInstance(msg).data(), config), counters)
    pm.poll_once()
    # a failed block read publishes BAD samples for its signals
    assert msg.published and msg.published[0][1].body["samples"][0]["quality"] == "BAD"


def test_start_and_stop_run_poll_threads():
    import time
    config = make_config(signals=[{"name": "Counter16", "table": "holding", "address": 0, "type": "uint16"}])
    conn = FakeConn()
    conn.holding[0] = 5
    msg = FakeMessaging()
    pm = PollManager(conn, config, SignalUpdatePublisher(FakeInstance(msg).data(), config), ClientMetrics())
    pm.start()
    time.sleep(0.2)
    pm.stop()
    assert any(t.endswith("/data/Counter16") for t, _ in msg.published)
