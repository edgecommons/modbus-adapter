"""Unit tests for the adapter-side instance resolution (``modbus_adapter.routing``).

The library owns addressing (topic token, body ``instance``, conflict refusal) and hands the
handler the resolved instance; what stays here is the configured-default policy (optional iff
exactly one device) and the existence check (``NO_SUCH_INSTANCE``) — D-SC-4."""
import pytest

from edgecommons.command_inbox import CommandException

from modbus_adapter.routing import resolve_instance


class _Device:
    """An opaque routing target — resolve_instance never looks inside it."""


PLC1 = _Device()
PLC2 = _Device()
ONE = {"plc1": PLC1}
TWO = {"plc1": PLC1, "plc2": PLC2}


# --- an addressed instance routes to that device ------------------------------------------------

def test_addressed_instance_routes():
    assert resolve_instance(TWO, "plc2") is PLC2


def test_addressed_instance_routes_on_a_single_device_adapter():
    assert resolve_instance(ONE, "plc1") is PLC1


def test_unknown_addressed_instance_is_no_such_instance():
    with pytest.raises(CommandException) as ei:
        resolve_instance(TWO, "plc9")
    assert ei.value.code == "NO_SUCH_INSTANCE"


def test_addressed_instance_never_falls_back_to_the_only_device():
    # an addressed instance addresses THAT instance — it never falls back to "the only device"
    with pytest.raises(CommandException) as ei:
        resolve_instance(ONE, "plc9")
    assert ei.value.code == "NO_SUCH_INSTANCE"


# --- no addressed instance: the configured default ----------------------------------------------

def test_unaddressed_single_device_routes():
    assert resolve_instance(ONE, None) is PLC1


def test_unaddressed_multi_device_is_bad_args():
    with pytest.raises(CommandException) as ei:
        resolve_instance(TWO, None)
    assert ei.value.code == "BAD_ARGS"
    assert "plc1" in str(ei.value) and "plc2" in str(ei.value)
