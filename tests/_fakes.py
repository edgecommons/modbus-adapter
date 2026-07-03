"""Shared test doubles: an in-memory Modbus connection, a fake instance handle / messaging client,
and a ServerConfiguration factory — so the UNS data/command/event surface unit-tests without a live
broker or a real PLC."""
import types
from collections import defaultdict

from ggcommons.uns import UnsClass

from modbus_adapter.config.server_configuration import ServerConfiguration


class FakeConn:
    """An in-memory holding-register + coil store that round-trips codec encode/decode."""

    def __init__(self, unit_id=1, connected=True):
        self.conn = types.SimpleNamespace(unit_id=unit_id)
        self.holding = defaultdict(int)
        self.coil = defaultdict(bool)
        self._connected = connected
        self.reconnected = 0
        self.reconnect_error = None

    def is_connected(self):
        return self._connected

    def read(self, table, address, count, unit):
        src = self.coil if table in ("coil", "discrete") else self.holding
        return [src[address + i] for i in range(count)]

    def write_coil(self, address, value, unit):
        self.coil[address] = bool(value)

    def write_registers(self, address, registers, unit):
        for i, r in enumerate(registers):
            self.holding[address + i] = r

    def reconnect(self):
        if self.reconnect_error:
            raise self.reconnect_error
        self.reconnected += 1
        self._connected = True
        return True


class FakeMessaging:
    """Records publish(topic, msg) calls."""

    def __init__(self):
        self.published = []          # list of (topic, msg)

    def publish(self, topic, msg):
        self.published.append((topic, msg))


class _FakeUns:
    def topic(self, cls: UnsClass, channel=None):
        base = f"ecv1/thing1/ModbusAdapter/plc1/{cls.value}"
        return f"{base}/{channel}" if channel else base


class _FakeBuilder:
    def __init__(self):
        self._payload = None

    def with_payload(self, payload):
        self._payload = payload
        return self

    def build(self):
        return {"body": self._payload}


class FakeInstance:
    """Stands in for gg.instance(id): uns().topic(...) + new_message(...).with_payload().build()."""

    def uns(self):
        return _FakeUns()

    def new_message(self, name, version):
        return _FakeBuilder()


class FakeEvents:
    def __init__(self):
        self.events = []             # list of (channel, body)

    def emit(self, channel, body):
        self.events.append((channel, body))


class FakePoller:
    def __init__(self):
        self.polled = 0

    def resolved_signals(self):
        return [{"name": "Counter16", "unitId": 1}]

    def poll_once(self):
        self.polled += 1
        return 2


class _CM:
    def __init__(self, inst):
        self._inst = inst

    def get_instance_config(self, iid):
        return self._inst if self._inst.get("id") == iid else {}


def make_config(signals=None, write_enabled=True, batch_ms=0, instance_id="plc1"):
    """Build a real ServerConfiguration around the given signals."""
    signals = signals if signals is not None else [
        {"name": "Counter16", "table": "holding", "address": 0, "type": "uint16"},
        {"name": "RWInt16", "table": "holding", "address": 10, "type": "int16"},
        {"name": "RunCmd", "table": "coil", "address": 0, "type": "bool"},
        {"name": "InCounter", "table": "input", "address": 5, "type": "uint16"},
    ]
    inst = {
        "id": instance_id,
        "connection": {"transport": "tcp", "host": "127.0.0.1", "port": 5020, "unitId": 1},
        "publish": {"batchMs": batch_ms},
        "write": {"enabled": write_enabled},
        "pollGroups": [{"id": "g", "pollIntervalMs": 500, "unitId": 1, "signals": signals}],
    }
    return ServerConfiguration(_CM(inst), {}, instance_id)
