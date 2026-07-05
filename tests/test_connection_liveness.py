"""The live liveness probe (#1c): ModbusConnection.is_connected() must reflect whether the LAST read
actually reached the slave — driven by the poll reads — NOT pymodbus's cached ``client.connected``,
which lags a socket that died mid-session. Regression for the stale-"connected" the E2E surfaced."""
import pytest
from pymodbus.exceptions import ConnectionException, ModbusIOException

from modbus_adapter import codec
from modbus_adapter.connection import ModbusConnection, ModbusError
from tests._fakes import make_config


class _Ok:
    def __init__(self, registers):
        self.registers = registers

    def isError(self):
        return False


class _SlaveErr:
    """A slave ExceptionResponse-like: a response ARRIVED (slave reachable) but the register errored."""

    def isError(self):
        return True


class _FakeClient:
    """pymodbus-client stand-in. ``connected`` stays True on purpose — the whole point is that
    is_connected() must NOT trust it once reads start failing."""

    def __init__(self):
        self.connected = True
        self.raise_exc = None
        self.response = None

    def _read(self, address, count=1, device_id=1):
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response

    # read() references all four (dict construction accesses every attribute).
    read_coils = _read
    read_discrete_inputs = _read
    read_holding_registers = _read
    read_input_registers = _read


def _conn():
    c = ModbusConnection(make_config())
    c.client = _FakeClient()
    c._connected = True
    return c


def test_a_successful_read_keeps_connected_true():
    c = _conn()
    c.client.response = _Ok([7])
    assert c.read(codec.HOLDING, 0, 1, 1) == [7]
    assert c.is_connected() is True


def test_a_raised_transport_error_flips_connected_false_even_though_pymodbus_stays_connected():
    c = _conn()
    c.client.connected = True  # pymodbus's stale flag still says "connected"
    c.client.raise_exc = ConnectionException("Connection refused")
    with pytest.raises(ModbusError):
        c.read(codec.HOLDING, 0, 1, 1)
    assert c.is_connected() is False  # the live probe caught the dead link


def test_a_modbus_io_exception_response_flips_connected_false():
    c = _conn()
    c.client.response = ModbusIOException("no response")
    with pytest.raises(ModbusError):
        c.read(codec.HOLDING, 0, 1, 1)
    assert c.is_connected() is False


def test_a_slave_exception_response_keeps_connected_true_reachable():
    c = _conn()
    c._connected = False  # start "down"
    c.client.response = _SlaveErr()  # illegal address etc. — the slave answered, so it's reachable
    with pytest.raises(ModbusError):
        c.read(codec.HOLDING, 0, 1, 1)
    assert c.is_connected() is True


def test_recovers_to_connected_on_the_next_good_read():
    c = _conn()
    c.client.raise_exc = ConnectionException("down")
    with pytest.raises(ModbusError):
        c.read(codec.HOLDING, 0, 1, 1)
    assert c.is_connected() is False
    c.client.raise_exc = None
    c.client.response = _Ok([1])
    c.read(codec.HOLDING, 0, 1, 1)
    assert c.is_connected() is True
