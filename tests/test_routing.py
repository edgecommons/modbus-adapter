"""Unit tests for the SOUTHBOUND §2.2 addressed-instance routing (``modbus_adapter.routing``):
the topic-addressed instance is authoritative (a conflicting body ``instance`` is ``BAD_ARGS``),
a topic-only address routes by the token, and a component-scoped delivery keeps the body routing
(selector optional iff exactly one device is configured)."""
import pytest

from edgecommons.command_inbox import CommandException

from modbus_adapter.routing import resolve_instance


class _Device:
    """An opaque routing target — resolve_instance never looks inside it."""


PLC1 = _Device()
PLC2 = _Device()
ONE = {"plc1": PLC1}
TWO = {"plc1": PLC1, "plc2": PLC2}


# --- case 1: the topic-addressed instance is authoritative --------------------------------------

def test_topic_addressed_routes_without_body_selector():
    assert resolve_instance(TWO, {}, "plc2") is PLC2


def test_topic_addressed_and_agreeing_body_selector_route():
    assert resolve_instance(TWO, {"instance": "plc2"}, "plc2") is PLC2


def test_conflicting_body_selector_is_bad_args():
    # both ids are valid on their own — the disagreement itself is the refusal
    with pytest.raises(CommandException) as ei:
        resolve_instance(TWO, {"instance": "plc2"}, "plc1")
    assert ei.value.code == "BAD_ARGS"
    assert "plc2" in str(ei.value) and "plc1" in str(ei.value)


def test_topic_addressed_wins_over_body_even_on_single_device():
    with pytest.raises(CommandException) as ei:
        resolve_instance(ONE, {"instance": "plc1"}, "plc9")
    assert ei.value.code == "BAD_ARGS"          # conflict is checked before existence


# --- case 2: topic-only addressing routes by the token ------------------------------------------

def test_topic_addressed_unknown_instance_is_no_such_instance():
    with pytest.raises(CommandException) as ei:
        resolve_instance(TWO, {}, "plc9")
    assert ei.value.code == "NO_SUCH_INSTANCE"


def test_topic_addressed_ignores_single_device_fallback():
    # a topic token addresses THAT instance — it never falls back to "the only device"
    with pytest.raises(CommandException) as ei:
        resolve_instance(ONE, {}, "plc9")
    assert ei.value.code == "NO_SUCH_INSTANCE"


# --- case 3: component-scoped deliveries keep the existing body routing -------------------------

def test_component_scoped_single_device_selector_optional():
    assert resolve_instance(ONE, {}, None) is PLC1


def test_component_scoped_body_selector_routes():
    assert resolve_instance(TWO, {"instance": "plc2"}, None) is PLC2


def test_component_scoped_multi_device_missing_selector_is_bad_args():
    with pytest.raises(CommandException) as ei:
        resolve_instance(TWO, {}, None)
    assert ei.value.code == "BAD_ARGS"


def test_component_scoped_unknown_selector_is_no_such_instance():
    with pytest.raises(CommandException) as ei:
        resolve_instance(TWO, {"instance": "plc9"}, None)
    assert ei.value.code == "NO_SUCH_INSTANCE"
