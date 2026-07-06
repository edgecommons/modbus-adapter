"""Unit tests for config parsing, defaults precedence, and topic resolution."""
import pytest

from modbus_adapter.config.connection_info import ConnectionInfo
from modbus_adapter.config.deadband_spec import DeadbandSpec
from modbus_adapter.config.server_configuration import ServerConfiguration
from modbus_adapter.config.signal_spec import SignalSpec


class FakeCM:
    """Minimal ConfigManager stand-in: ServerConfiguration only needs per-instance config now that
    topic construction moved to the UNS builder."""

    def __init__(self, instances, component="modbus-adapter"):
        self._instances = {i["id"]: i for i in instances}
        self._component = component

    def get_instance_config(self, iid):
        return self._instances.get(iid, {})


# --- SignalSpec ------------------------------------------------------------------------------
def test_signalspec_valid_and_helpers():
    t = SignalSpec.from_dict({"name": "Temp", "table": "holding", "address": 4, "type": "float32",
                              "scale": 0.1, "wordOrder": "little"})
    assert t.unit_length() == 2
    assert t.signal_id(2) == "u2/holding/4/float32"
    assert t.address_dict(2) == {"unitId": 2, "table": "holding", "address": 4, "type": "float32",
                                 "wordOrder": "little", "byteOrder": "big"}


def test_signalspec_coil_and_bit_lengths():
    coil = SignalSpec.from_dict({"name": "Run", "table": "coil", "address": 0})
    assert coil.type == "bool" and coil.unit_length() == 1
    bitsig = SignalSpec.from_dict({"name": "Alarm", "table": "holding", "address": 10, "type": "bool", "bit": 3})
    assert bitsig.unit_length() == 1
    assert bitsig.address_dict(1)["bit"] == 3
    strsig = SignalSpec.from_dict({"name": "Label", "table": "holding", "address": 20, "type": "string", "count": 5})
    assert strsig.unit_length() == 5


@pytest.mark.parametrize("bad", [
    {"table": "holding", "address": 0},                                   # no name
    {"name": "x", "table": "bogus", "address": 0},                        # bad table
    {"name": "x", "table": "holding"},                                    # no address
    {"name": "x", "table": "holding", "address": 0, "type": "string"},    # string needs count
    {"name": "x", "table": "coil", "address": 0, "type": "int16"},        # coil must be bool
    {"name": "x", "table": "coil", "address": 0, "bit": 1},               # bit only on register bool
    {"name": "x", "table": "holding", "address": 0, "type": "int16", "bit": 1},  # bit needs bool
])
def test_signalspec_validation_errors(bad):
    with pytest.raises(ValueError):
        SignalSpec.from_dict(bad)


# --- DeadbandSpec ----------------------------------------------------------------------------
def test_deadband():
    assert DeadbandSpec().exceeds(None, 5) is True            # first value always publishes
    assert DeadbandSpec("none").exceeds(5, 5) is False
    assert DeadbandSpec("none").exceeds(5, 6) is True
    ab = DeadbandSpec("absolute", 0.5)
    assert ab.exceeds(10.0, 10.4) is False
    assert ab.exceeds(10.0, 10.6) is True
    pc = DeadbandSpec("percent", 10)
    assert pc.exceeds(100.0, 105.0) is False
    assert pc.exceeds(100.0, 111.0) is True
    assert pc.exceeds(0.0, 1.0) is True                       # base 0 -> any change
    assert DeadbandSpec("absolute", 1).exceeds("a", "b") is True  # non-numeric -> any change


# --- ConnectionInfo --------------------------------------------------------------------------
def test_connection_defaults_and_transports():
    c = ConnectionInfo({})
    assert c.transport == "tcp" and c.host == "127.0.0.1" and c.port == 502 and c.unit_id == 1
    assert c.timeout_s == 1.0
    rtu = ConnectionInfo({"transport": "rtu", "serialPort": "COM3", "baudRate": 19200, "unitId": 7})
    assert rtu.transport == "rtu" and rtu.serial_port == "COM3" and rtu.baud_rate == 19200 and rtu.unit_id == 7
    assert ConnectionInfo({"transport": "rtutcp", "host": "h", "port": 5020}).port == 5020
    with pytest.raises(ValueError):
        ConnectionInfo({"transport": "bogus"})


# --- ServerConfiguration ---------------------------------------------------------------------
def test_server_configuration_precedence():
    inst = {
        "id": "plc1",
        "connection": {"transport": "tcp", "host": "10.0.0.5", "port": 1502, "unitId": 3},
        "defaults": {"pollIntervalMs": 250},
        "publish": {"batchMs": 100},
        "write": {"enabled": True},
        "pollGroups": [{
            "id": "g1", "pollIntervalMs": 500,
            "signals": [{"name": "Temp", "table": "holding", "address": 0, "type": "float32", "scale": 0.1}],
        }],
    }
    glob = {"defaults": {"pollIntervalMs": 1000, "maxGap": 4, "publishMode": "always"}}
    sc = ServerConfiguration(FakeCM([inst]), glob, "plc1")

    assert sc.connection.host == "10.0.0.5" and sc.connection.unit_id == 3
    assert sc.poll_interval_ms == 250        # instance overrides global
    assert sc.max_gap == 4                    # falls back to global
    assert sc.publish_mode == "always"        # falls back to global
    assert sc.batch_ms == 100
    assert sc.write_enabled is True

    g = sc.poll_groups[0]
    assert g.poll_interval_ms == 500          # group override
    assert g.unit_id == 3                      # inherits connection unit
    assert g.publish_mode == "always"          # inherits server
    assert g.max_gap == 4
    assert len(sc.all_signals()) == 1


def test_write_disabled_by_default():
    inst = {"id": "plc9", "pollGroups": []}
    sc = ServerConfiguration(FakeCM([inst]), {}, "plc9")
    assert sc.write_enabled is False


def test_uns_data_topic_and_identity():
    """Data-plane addressing now comes from the UNS builder (gg.instance(id).uns()), not a config
    template: the channel is the sanitized signal name and the topic is device/component/instance-shaped."""
    from edgecommons.config.manager.config_manager import ConfigManager
    from edgecommons.messaging.identity import HierEntry, MessageIdentity
    from edgecommons.uns import Uns, UnsClass

    identity = MessageIdentity(
        [HierEntry("site", "lab"), HierEntry("device", "thing1")], "modbus-adapter"
    ).with_instance("plc1")
    uns = Uns(identity, include_root=False)                       # rootless (default) grammar
    assert uns.topic(UnsClass.DATA, ConfigManager.sanitize("Temp")) == \
        "ecv1/thing1/modbus-adapter/plc1/data/Temp"
    # A signal name carrying reserved topic chars is sanitized to a single valid channel token.
    assert uns.topic(UnsClass.DATA, ConfigManager.sanitize("Line/1#Temp")) == \
        "ecv1/thing1/modbus-adapter/plc1/data/Line_1_Temp"
