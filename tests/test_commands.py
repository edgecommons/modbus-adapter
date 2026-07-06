"""Unit tests for the command-inbox verb surface (CommandService) — read / write / status / signals /
reconnect / repoll — using in-memory fakes (no live broker or PLC)."""
import pytest

from edgecommons.command_inbox import CommandException

from modbus_adapter.command_service import CommandService
from tests._fakes import FakeConn, FakeEvents, FakePoller, make_config


def _svc(conn=None, config=None, poller=None):
    from modbus_adapter.metrics import ClientMetrics
    conn = conn or FakeConn()
    config = config or make_config()
    events = FakeEvents()
    poller = poller or FakePoller()
    svc = CommandService(conn, events, config, ClientMetrics(), poller)
    return svc, conn, events, poller


def test_read_by_name():
    svc, conn, _, _ = _svc()
    conn.holding[0] = 4321
    res = svc.read({"signals": [{"name": "Counter16"}]})
    assert res["id"] == "plc1"
    assert len(res["reads"]) == 1
    r = res["reads"][0]
    assert r["value"] == 4321 and r["quality"] == "GOOD"
    assert r["signal"]["id"] == "u1/holding/0/uint16"


def test_read_explicit_ref_and_unknown_skipped():
    svc, conn, _, _ = _svc()
    conn.holding[10] = 7
    res = svc.read({"signals": [{"table": "holding", "address": 10, "type": "int16"},
                                {"name": "DoesNotExist"}]})
    # explicit ref resolves; the unknown name is skipped (not an error)
    assert len(res["reads"]) == 1 and res["reads"][0]["value"] == 7


def test_read_error_marks_bad(monkeypatch):
    svc, conn, _, _ = _svc()

    def boom(*a, **k):
        raise RuntimeError("timeout")
    monkeypatch.setattr(conn, "read", boom)
    res = svc.read({"signals": [{"name": "Counter16"}]})
    assert res["reads"][0]["quality"] == "BAD" and "timeout" in res["reads"][0]["qualityRaw"]


def test_write_roundtrip_and_evt():
    svc, conn, events, _ = _svc()
    res = svc.write({"writes": [{"name": "RWInt16", "value": -1234}]})
    assert res["written"] == 1 and res["results"][0]["ok"] is True
    # value round-trips through the in-memory store
    back = svc.read({"signals": [{"name": "RWInt16"}]})
    assert back["reads"][0]["value"] == -1234
    # an evt/write audit record was emitted through the events() facade wrapper
    assert events.events and events.events[0][0] == "write"
    assert events.events[0][2] == "RWInt16" and events.events[0][1] is True


def test_write_coil():
    svc, conn, _, _ = _svc()
    res = svc.write({"writes": [{"name": "RunCmd", "value": True}]})
    assert res["written"] == 1 and conn.coil[0] is True


def test_write_disabled_raises():
    svc, _, _, _ = _svc(config=make_config(write_enabled=False))
    with pytest.raises(CommandException) as ei:
        svc.write({"writes": [{"name": "RWInt16", "value": 1}]})
    assert ei.value.code == "WRITE_DISABLED"


def test_write_readonly_table_reported():
    svc, _, events, _ = _svc()
    res = svc.write({"writes": [{"name": "InCounter", "value": 3}]})
    assert res["written"] == 0
    assert res["results"][0]["ok"] is False and "read-only" in res["results"][0]["error"]


def test_write_missing_value_reported():
    svc, _, _, _ = _svc()
    res = svc.write({"writes": [{"name": "RWInt16"}]})
    assert res["results"][0]["ok"] is False and "value" in res["results"][0]["error"]


def test_write_single_body_form():
    # a bare {name,value} (no "writes" array) is accepted as one write
    svc, conn, _, _ = _svc()
    res = svc.write({"name": "RWInt16", "value": 9})
    assert res["written"] == 1 and conn.holding[10] == 9


def test_status():
    svc, _, _, _ = _svc()
    res = svc.status()
    assert res["connected"] is True and "read" in res["metrics"] and "write" in res["metrics"]


def test_signals():
    svc, _, _, poller = _svc()
    res = svc.signals()
    assert res["signals"] == poller.resolved_signals()


def test_reconnect_ok():
    svc, conn, _, _ = _svc()
    res = svc.reconnect()
    assert res["connected"] is True and conn.reconnected == 1


def test_reconnect_failure_raises():
    conn = FakeConn()
    conn.reconnect_error = RuntimeError("no route")
    svc, _, _, _ = _svc(conn=conn)
    with pytest.raises(CommandException) as ei:
        svc.reconnect()
    assert ei.value.code == "RECONNECT_FAILED"


def test_repoll():
    svc, _, _, poller = _svc()
    res = svc.repoll()
    assert res["polled"] == 2 and poller.polled == 1


def test_write_unresolvable_ref_reported():
    svc, _, _, _ = _svc()
    res = svc.write({"writes": [{"unitId": 1, "value": 5}]})   # no name, no table/address
    assert res["written"] == 0 and res["results"][0]["ok"] is False


def test_write_bit_not_supported():
    config = make_config(signals=[
        {"name": "Alarm3", "table": "holding", "address": 41, "type": "bool", "bit": 3},
    ])
    svc, _, _, _ = _svc(config=config)
    res = svc.write({"writes": [{"name": "Alarm3", "value": True}]})
    assert res["written"] == 0 and "bit writes" in res["results"][0]["error"]


def test_write_encode_failure_reported():
    svc, conn, events, _ = _svc()

    def boom(*a, **k):
        raise RuntimeError("bus error")
    conn.write_registers = boom
    res = svc.write({"writes": [{"name": "RWInt16", "value": 7}]})
    assert res["results"][0]["ok"] is False and "bus error" in res["results"][0]["error"]
    assert events.events[0][1] is False


def test_read_unresolvable_ref_skipped():
    svc, _, _, _ = _svc()
    res = svc.read({"signals": [{"unitId": 1}]})   # neither a known name nor table+address
    assert res["reads"] == []
