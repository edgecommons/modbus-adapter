"""Coordinates one Modbus device instance: connection + poll manager + publisher + command service +
health, plus a tick that flushes batched publishes and emits health. Mirrors OpcUaDevice."""
import logging
import threading

from .command_service import CommandService
from .connection import ModbusConnection
from .health import HealthMetrics
from .metrics import ClientMetrics
from .poll_manager import PollManager
from .publisher import SignalUpdatePublisher

LOGGER = logging.getLogger("modbus_adapter.device")


class ModbusDevice:
    def __init__(self, config_manager, messaging, metrics, credentials, config):
        # credentials unused: classic Modbus has no auth (network-level security). Kept for symmetry.
        self.config = config
        self._counters = ClientMetrics()
        self._health = HealthMetrics(metrics, config_manager, config.id, self._counters)

        self._connection = ModbusConnection(config)
        self._connection.connect()                      # blocks/retries until connected
        self._health.emit(True)

        self._publisher = SignalUpdatePublisher(messaging, config_manager, config)
        self._poller = PollManager(self._connection, config, self._publisher, self._counters)
        self._poller.start()

        self._commands = CommandService(self._connection, messaging, config_manager, config,
                                        self._counters, self._poller)
        self._commands.subscribe()

        self._stop = threading.Event()
        tick = (config.batch_ms / 1000.0) if config.batch_ms > 0 else 5.0
        self._ticker = threading.Thread(target=self._tick_loop, args=(tick,),
                                        name=f"tick-{config.id}", daemon=True)
        self._ticker.start()
        LOGGER.info("[%s] device started", config.id)

    def _tick_loop(self, tick):
        while not self._stop.wait(tick):
            self._publisher.flush()
            self._health.emit(self._connection.is_connected())

    def stop(self):
        self._stop.set()
        self._poller.stop()
        self._publisher.flush()
        self._connection.close()
