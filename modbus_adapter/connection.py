"""Owns the pymodbus client for one instance: builds it for the configured transport, connects with
retry/backoff, and exposes simple table read/write helpers. (pymodbus 3.x: keyword-only ``count`` and
``device_id``; RTU-over-TCP is a TCP client with the RTU framer.)"""
import logging
import threading
import time

from pymodbus import FramerType
from pymodbus.client import ModbusSerialClient, ModbusTcpClient

from . import codec
from .config.connection_info import RTU, RTU_TCP

LOGGER = logging.getLogger("modbus_adapter.connection")
RETRY_S = 5.0


class ModbusError(Exception):
    """A Modbus read/write failed (exception response, timeout, or I/O error)."""


class ModbusConnection:
    def __init__(self, config):
        self.config = config                      # ServerConfiguration
        self.conn = config.connection             # ConnectionInfo
        self.client = None
        self._connected = False

    def is_connected(self) -> bool:
        return bool(self.client is not None and getattr(self.client, "connected", False))

    def connect(self):
        """Block, retrying every RETRY_S, until the client is created and connected."""
        while self.client is None:
            try:
                client = self._create()
                if not client.connect():
                    raise ModbusError("connect() returned False")
                self.client = client
                self._connected = True
                LOGGER.info("[%s] connected to %s", self.config.id, self.conn.describe())
            except Exception as e:  # noqa: BLE001 - retry on anything
                self.client = None
                self._connected = False
                LOGGER.error("[%s] unable to connect to %s: %s. Retrying in %ss...",
                             self.config.id, self.conn.describe(), e, int(RETRY_S))
                time.sleep(RETRY_S)
        return self.client

    def _create(self):
        c = self.conn
        if c.transport == RTU:
            return ModbusSerialClient(c.serial_port, framer=FramerType.RTU, baudrate=c.baud_rate,
                                      bytesize=c.byte_size, parity=c.parity, stopbits=c.stop_bits,
                                      timeout=c.timeout_s)
        framer = FramerType.RTU if c.transport == RTU_TCP else FramerType.SOCKET
        return ModbusTcpClient(c.host, port=c.port, framer=framer, timeout=c.timeout_s)

    # --- reads / writes (raise ModbusError on failure) -------------------------------------
    def read(self, table, address, count, unit_id):
        """Return a list of bits (coil/discrete) or registers (holding/input)."""
        readers = {
            codec.COIL: self.client.read_coils,
            codec.DISCRETE: self.client.read_discrete_inputs,
            codec.HOLDING: self.client.read_holding_registers,
            codec.INPUT: self.client.read_input_registers,
        }
        rr = readers[table](address, count=count, device_id=unit_id)
        if rr is None or rr.isError():
            raise ModbusError(str(rr))
        data = rr.bits if table in codec.BIT_TABLES else rr.registers
        return list(data[:count])

    def write_coil(self, address, value, unit_id):
        rr = self.client.write_coil(address, bool(value), device_id=unit_id)
        if rr is None or rr.isError():
            raise ModbusError(str(rr))

    def write_registers(self, address, registers, unit_id):
        if len(registers) == 1:
            rr = self.client.write_register(address, registers[0], device_id=unit_id)
        else:
            rr = self.client.write_registers(address, registers, device_id=unit_id)
        if rr is None or rr.isError():
            raise ModbusError(str(rr))

    def close(self):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:  # noqa: BLE001
                pass
            self._connected = False
