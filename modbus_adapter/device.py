"""Coordinates one Modbus device instance: connection + poll manager + publisher + command service +
health, plus a tick that flushes batched publishes and emits health. Mirrors OpcUaDevice.

Each device owns a ``gg.instance(id)`` handle: the publisher and event emitter mint this instance's
UNS ``data``/``evt`` topics through it, and every message it builds is stamped with the top-level
``identity`` element carrying this instance token. The on-demand command surface (``self.commands``) is
served through the shared command inbox — ``main.py`` registers the verbs and dispatches into this
device by the request body's ``instance`` selector; the device no longer subscribes any topic itself.
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
        messaging = gg.get_messaging()
        metrics = gg.get_metrics()
        self.config = config
        # The instance-scoped handle: its uns() mints this instance's data/evt topics and its
        # new_message() stamps the config-resolved identity with this instance token.
        self._instance = gg.instance(config.id)
        self._events = EventEmitter(messaging, self._instance)

        self._counters = ClientMetrics()
        self._health = HealthMetrics(metrics, config_manager, config.id, self._counters)

        self._connection = ModbusConnection(config)
        self._connection.connect()                      # blocks/retries until connected
        self._connected = True
        self._health.emit(True)
        self._events.emit("connection", {"instance": config.id, "connected": True,
                                          "endpoint": config.connection.describe()})

        self._publisher = SignalUpdatePublisher(messaging, self._instance, config)
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
                self._events.emit("connection", {"instance": self.config.id, "connected": connected,
                                                  "endpoint": self.config.connection.describe()})

    def stop(self):
        self._stop.set()
        self._poller.stop()
        self._publisher.flush()
        self._connection.close()
