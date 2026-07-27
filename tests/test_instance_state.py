"""Unit tests for the single per-instance state model (D-SC-7): the ``sb/status`` reply and the
``state`` keepalive's ``instances[]`` read the same tokens, and the published wire element carries
them."""
from modbus_adapter.command_service import CommandService
from modbus_adapter.instance_state import (
    BACKOFF,
    CONNECTING,
    ONLINE,
    PAUSED,
    device_state,
    instance_connectivity,
)
from modbus_adapter.metrics import ClientMetrics
from modbus_adapter.pause import PauseState
from tests._fakes import FakeConn, FakeEvents, FakePoller, make_config


class _Device:
    """A ``ModbusDevice`` stand-in that answers ``state()`` from a real ``CommandService``, exactly
    as the device does."""

    def __init__(self, conn, pause):
        self.commands = CommandService(conn, FakeEvents(), make_config(), ClientMetrics(),
                                       FakePoller(), pause_state=pause)
        self._conn = conn

    def is_connected(self):
        return self._conn.is_connected()

    def state(self):
        return self.commands.state()

    @property
    def endpoint(self):
        return "tcp://127.0.0.1:5020 unit=1"


def _device(connected=True, paused=False):
    pause = PauseState()
    pause.set(paused)
    return _Device(FakeConn(connected=connected), pause)


# --- the model ----------------------------------------------------------------------------------

def test_device_state_tokens():
    assert device_state(paused=False, connected=True) == ONLINE
    assert device_state(paused=False, connected=False) == BACKOFF
    assert device_state(paused=True, connected=True) == PAUSED
    # link truth wins: a paused instance whose link is down reads BACKOFF, never PAUSED
    assert device_state(paused=True, connected=False) == BACKOFF


# --- the wire element ---------------------------------------------------------------------------

def test_online_instance_wire_element():
    entries = instance_connectivity(["plc1"], {"plc1": _device()})
    assert [e.to_dict() for e in entries] == [
        {"instance": "plc1", "connected": True, "state": ONLINE,
         "detail": "tcp://127.0.0.1:5020 unit=1"},
    ]


def test_paused_instance_publishes_paused_state():
    entries = instance_connectivity(["plc1"], {"plc1": _device(paused=True)})
    element = entries[0].to_dict()
    assert element["state"] == PAUSED
    # the normalized flag still carries live liveness beside the administrative state
    assert element["connected"] is True


def test_disconnected_instance_is_backoff():
    entries = instance_connectivity(["plc1"], {"plc1": _device(connected=False)})
    element = entries[0].to_dict()
    assert element["state"] == BACKOFF and element["connected"] is False


def test_a_break_while_paused_reports_backoff_not_paused():
    # Link truth wins: the keepalive shows the link is down; the pause stays visible on sb/status.
    device = _device(connected=False, paused=True)
    element = instance_connectivity(["plc1"], {"plc1": device})[0].to_dict()
    assert element["state"] == BACKOFF and element["connected"] is False
    assert device.commands.status()["paused"] is True


def test_instance_without_a_device_yet_is_connecting():
    entries = instance_connectivity(["plc1", "plc2"], {"plc1": _device()})
    assert [e.to_dict() for e in entries] == [
        {"instance": "plc1", "connected": True, "state": ONLINE,
         "detail": "tcp://127.0.0.1:5020 unit=1"},
        {"instance": "plc2", "connected": False, "state": CONNECTING},
    ]


# --- one model, two surfaces --------------------------------------------------------------------

def test_status_and_keepalive_agree_across_a_pause():
    device = _device()
    assert device.commands.status()["state"] == ONLINE
    assert instance_connectivity(["plc1"], {"plc1": device})[0].to_dict()["state"] == ONLINE

    device.commands.pause()
    assert device.commands.status()["state"] == PAUSED
    assert instance_connectivity(["plc1"], {"plc1": device})[0].to_dict()["state"] == PAUSED

    device.commands.resume()
    assert device.commands.status()["state"] == ONLINE
    assert instance_connectivity(["plc1"], {"plc1": device})[0].to_dict()["state"] == ONLINE
