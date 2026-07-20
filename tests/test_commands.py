"""Unit tests for the command-inbox verb surface (CommandService) — read / write / status / signals /
browse / pause / resume / reconnect / repoll — using in-memory fakes (no live broker or PLC)."""
import pytest

from edgecommons.command_inbox import CommandException

from modbus_adapter.command_service import CommandService, panels
from modbus_adapter.pause import PauseState
from tests._fakes import FakeConn, FakeEvents, FakePoller, make_config


def _svc(conn=None, config=None, poller=None, pause_state=None):
    from modbus_adapter.metrics import ClientMetrics
    conn = conn or FakeConn()
    config = config or make_config()
    events = FakeEvents()
    poller = poller or FakePoller()
    pause_state = pause_state if pause_state is not None else PauseState()
    svc = CommandService(conn, events, config, ClientMetrics(), poller, pause_state=pause_state)
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


def test_write_not_allowed_raises():
    # empty writes.allow => the whole batch is refused before any device I/O -> WRITE_NOT_ALLOWED
    svc, _, _, _ = _svc(config=make_config(writes_allow=[]))
    with pytest.raises(CommandException) as ei:
        svc.write({"writes": [{"name": "RWInt16", "value": 1}]})
    assert ei.value.code == "WRITE_NOT_ALLOWED"


def test_write_allow_list_refuses_per_entry_before_device_io():
    # allow only RWInt16: the disallowed RunCmd is refused per-entry (never written), RWInt16 succeeds
    svc, conn, _, _ = _svc(config=make_config(writes_allow=["u1/holding/10/int16"]))
    res = svc.write({"writes": [{"name": "RWInt16", "value": 5}, {"name": "RunCmd", "value": True}]})
    assert res["written"] == 1
    by_name = {r["signal"]: r for r in res["results"]}
    assert by_name["RWInt16"]["ok"] is True
    assert by_name["RunCmd"]["ok"] is False and "writes.allow" in by_name["RunCmd"]["error"]
    assert conn.coil.get(0) in (False, None)          # the refused coil write never reached the device


def test_write_all_failed_raises_write_failed():
    svc, conn, _, _ = _svc()

    def boom(*a, **k):
        raise RuntimeError("bus error")
    conn.write_registers = boom
    with pytest.raises(CommandException) as ei:
        svc.write({"writes": [{"name": "RWInt16", "value": 7}]})
    assert ei.value.code == "WRITE_FAILED"


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
    assert res["connected"] is True and res["paused"] is False
    assert "read" in res["metrics"] and "write" in res["metrics"]


def test_pause_resume_idempotent_and_status_reflects_it():
    pause = PauseState()
    svc, _, _, _ = _svc(pause_state=pause)
    p1 = svc.pause()
    assert p1 == {"id": "plc1", "paused": True, "changed": True}
    assert svc.pause()["changed"] is False               # idempotent
    assert svc.status()["paused"] is True and pause.is_paused() is True
    r1 = svc.resume()
    assert r1 == {"id": "plc1", "paused": False, "changed": True}
    assert svc.resume()["changed"] is False              # idempotent
    assert pause.is_paused() is False


def test_repoll_refused_while_paused():
    svc, _, _, poller = _svc()
    svc.pause()
    with pytest.raises(CommandException) as ei:
        svc.repoll()
    assert ei.value.code == "BAD_ARGS" and poller.polled == 0
    svc.resume()
    assert svc.repoll()["polled"] == 2 and poller.polled == 1


def test_browse_pages_configured_inventory():
    svc, _, _, _ = _svc()
    # page 1 of 1 (2 signals, default max) -> no cursor
    res = svc.browse({})
    ids = [e["id"] for e in res["entries"]]
    assert ids == ["u1/holding/0/uint16", "u1/holding/10/int16"]
    assert res["entries"][0]["type"] == "uint16" and "cursor" not in res
    # paging: max=1 -> a cursor to the next page
    first = svc.browse({"max": 1})
    assert len(first["entries"]) == 1 and first["cursor"] == "1"
    second = svc.browse({"cursor": first["cursor"], "max": 1})
    assert second["entries"][0]["id"] == "u1/holding/10/int16" and "cursor" not in second


def test_browse_bad_cursor():
    svc, _, _, _ = _svc()
    with pytest.raises(CommandException) as ei:
        svc.browse({"cursor": "nope"})
    assert ei.value.code == "BAD_ARGS"


def test_panels_trio():
    ps = panels()
    assert [p["id"] for p in ps] == ["overview", "signals", "diagnostics"]
    assert [p["order"] for p in ps] == [10, 20, 30]
    assert all(p["scope"] == "instance" for p in ps)
    # every bound verb is one the adapter actually serves
    served = {"sb/status", "sb/read", "sb/write", "sb/signals", "sb/browse", "sb/pause",
              "sb/resume", "reconnect", "repoll"}
    for p in ps:
        assert set(p["verbs"]) <= served


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


def test_write_encode_failure_reported_per_entry_in_mixed_batch():
    # A mixed batch keeps per-entry granularity: the coil write succeeds, the register write fails,
    # so nothing is raised (not an all-failed batch) and the failure is reported per-entry + audited.
    svc, conn, events, _ = _svc()

    def boom(*a, **k):
        raise RuntimeError("bus error")
    conn.write_registers = boom
    res = svc.write({"writes": [{"name": "RunCmd", "value": True}, {"name": "RWInt16", "value": 7}]})
    by_name = {r["signal"]: r for r in res["results"]}
    assert by_name["RunCmd"]["ok"] is True
    assert by_name["RWInt16"]["ok"] is False and "bus error" in by_name["RWInt16"]["error"]
    assert any(e[0] == "write" and e[1] is False and e[2] == "RWInt16" for e in events.events)


def test_read_unresolvable_ref_skipped():
    svc, _, _, _ = _svc()
    res = svc.read({"signals": [{"unitId": 1}]})   # neither a known name nor table+address
    assert res["reads"] == []
