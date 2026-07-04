"""Coordinates one Modbus device instance: connection + poll manager + publisher + command service +
health, plus a tick that flushes batched publishes and emits health. Mirrors OpcUaDevice.

Each device owns a ``gg.instance(id)`` handle: the publisher and event emitter publish through this
instance's ``data()``/``events()`` facades (``docs/platform/DESIGN-class-facades.md``), which mint
the UNS ``data``/``evt`` topics and stamp the top-level ``identity`` element carrying this instance
token. The on-demand command surface (``self.commands``) is served through the shared command inbox
— ``main.py`` registers the verbs and dispatches into this device by the request body's ``instance``
selector; the device no longer subscribes any topic itself.
"""
import logging
import threading

from .command_service import CommandService
from .connection import ModbusConnection
from .events import EventEmitter
from .health import HealthMetrics
from .metrics import ClientMetrics
from .poll_manager import PollManager
from .publisher import SignalUpdatePublisher

LOGGER = logging.getLogger("modbus_adapter.device")


class ModbusDevice:
    def __init__(self, gg, config):
        # credentials unused: classic Modbus has no auth (network-level security).
        self._gg = gg
        config_manager = gg.get_config_manager()
        metrics = gg.get_metrics()
        self.config = config
        # The instance-scoped handle: its data()/events() facades mint this instance's data/evt
        # topics and stamp the config-resolved identity with this instance token.
        self._instance = gg.instance(config.id)
        self._events = EventEmitter(self._instance.events())

        self._counters = ClientMetrics()
        self._health = HealthMetrics(metrics, config_manager, config.id, self._counters)

        self._connection = ModbusConnection(config)
        self._connection.connect()                      # blocks/retries until connected
        self._connected = True
        self._health.emit(True)
        self._events.connection(True, {"endpoint": config.connection.describe()})

        self._publisher = SignalUpdatePublisher(self._instance.data(), config)
        self._poller = PollManager(self._connection, config, self._publisher, self._counters)
        self._poller.start()

        # The command surface (read/write/status/signals/reconnect/repoll). No subscription here —
        # main.py registers the verbs on gg.get_commands() and dispatches into this object.
        self.commands = CommandService(self._connection, self._events, config,
                                       self._counters, self._poller)

        self._stop = threading.Event()
        tick = (config.batch_ms / 1000.0) if config.batch_ms > 0 else 5.0
        self._ticker = threading.Thread(target=self._tick_loop, args=(tick,),
                                        name=f"tick-{config.id}", daemon=True)
        self._ticker.start()
        LOGGER.info("[%s] device started", config.id)

    def _tick_loop(self, tick):
        while not self._stop.wait(tick):
            self._publisher.flush()
            connected = self._connection.is_connected()
            self._health.emit(connected)
            if connected != self._connected:
                self._connected = connected
                self._events.connection(connected, {"endpoint": self.config.connection.describe()})

    def stop(self):
        self._stop.set()
        self._poller.stop()
        self._publisher.flush()
        self._connection.close()

    def is_connected(self) -> bool:
        """Whether this device's Modbus slave connection is currently up — the per-instance
        connectivity reported in the main state keepalive's instances[] (#1c)."""
        return self._connection.is_connected()

    @property
    def endpoint(self) -> str:
        """Human description of the slave connection (host:port/unit) — the connectivity detail."""
        return self.config.connection.describe()
