"""Shared test doubles: an in-memory Modbus connection, a recording messaging client + a real
``EdgeCommonsInstance`` bound to it (so the publisher/events unit tests exercise the real edgecommons
``data()``/``events()`` facades -- DESIGN-class-facades -- instead of a hand-rolled uns()/
new_message() fake), and a ServerConfiguration factory."""
import types
from collections import defaultdict

from edgecommons.edgecommons_instance import EdgeCommonsInstance
from edgecommons.messaging.identity import HierEntry, MessageIdentity

from modbus_adapter.config.server_configuration import ServerConfiguration

#: The identity the fake EdgeCommonsInstance is bound to -- mirrors the pre-migration fake's
#: ``ecv1/thing1/modbus-adapter/plc1/...`` topic shape (device=thing1, component=modbus-adapter).
IDENTITY = MessageIdentity([HierEntry("device", "thing1")], "modbus-adapter", "plc1")


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
    """Records publish(topic, msg)/publish_to_iot_core(topic, msg, qos) calls -- the
    ``messaging_client`` the ``data()``/``events()`` facades publish through."""

    def __init__(self):
        self.published = []          # list of (topic, Message)

    def publish(self, topic, msg):
        self.published.append((topic, msg))

    def publish_to_iot_core(self, topic, msg, qos):
        self.published.append((topic, msg))


class _FakeConfigManager:
    """Minimal ``ConfigManager`` double: just enough for ``MessageBuilder``/``DataFacade``/
    ``EventsFacade`` (component identity, tag config, and the ``publish.channel`` lookups, which
    default to "nothing configured" -> LOCAL)."""

    def __init__(self, identity=IDENTITY):
        self._identity = identity

    def get_component_identity(self):
        return self._identity

    def get_tag_config(self):
        return None

    def get_instance_config(self, instance_id):
        return {}

    def get_global_config(self):
        return {}


def FakeInstance(messaging=None, instance_id="plc1"):
    """A real :class:`~edgecommons.edgecommons_instance.EdgeCommonsInstance` bound to a fake identity/messaging --
    exercises the real ``data()``/``events()`` facades (DESIGN-class-facades) so the publisher/
    events tests pin the real body/topic contract instead of a hand-rolled fake. Drop-in
    replacement for the pre-migration ``FakeInstance`` (same default topic shape:
    ``ecv1/thing1/modbus-adapter/plc1/...``)."""
    messaging = messaging if messaging is not None else FakeMessaging()
    return EdgeCommonsInstance(instance_id, _FakeConfigManager(), False, messaging_client=messaging)


class FakeEvents:
    """Stands in for :class:`~modbus_adapter.events.EventEmitter` in ``CommandService``/
    ``ModbusDevice`` tests: records ``connection``/``write`` calls without touching the bus."""

    def __init__(self):
        self.events = []             # list of (kind, *args)

    def connection(self, connected, context=None):
        self.events.append(("connection", connected, context))

    def write(self, ok, signal_name, value, error=None):
        self.events.append(("write", ok, signal_name, value, error))


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
