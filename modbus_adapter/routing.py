"""Configured-device resolution for the shared command inbox (SOUTHBOUND.md §2.2 / D-SC-4).

The library command inbox owns **addressing**. It subscribes both D-U28 command scopes —
``ecv1/{device}/modbus-adapter/cmd/#`` (component scope) and
``ecv1/{device}/modbus-adapter/+/cmd/#`` (instance scope) — extracts the topic's instance token and
the request body's ``instance`` field, refuses a disagreement between the two with ``BAD_ARGS``
before dispatch, and hands every handler the resolved ``addressed_instance`` (the topic token, else
the body-named instance, else ``None``). No handler runs on an addressing error.

What the library cannot know is this adapter's configuration, so exactly two policies stay here:

- **optional-iff-one** — an absent ``addressed_instance`` routes to the sole ready device; with
  several devices it is ``BAD_ARGS``.
- **``NO_SUCH_INSTANCE``** — an addressed instance this adapter does not serve.
"""
from edgecommons.command_inbox import CommandException


def resolve_instance(devices, addressed_instance):
    """Pick the target device for one command delivery.

    :param devices: the ready devices, ``{instance_id: ModbusDevice}``
    :param addressed_instance: the library-resolved addressed instance (the topic's instance token,
        else the body's ``instance`` field), or ``None`` when the delivery names no instance
    :returns: the addressed device
    :raises CommandException: ``BAD_ARGS`` when no instance is addressed and several are
        configured, ``NO_SUCH_INSTANCE`` when the addressed instance is unknown
    """
    if addressed_instance is None:
        if len(devices) == 1:
            return next(iter(devices.values()))
        raise CommandException(
            "BAD_ARGS", f"the request must address an instance (configured: {sorted(devices)})")
    device = devices.get(addressed_instance)
    if device is None:
        raise CommandException(
            "NO_SUCH_INSTANCE",
            f"no ready device instance '{addressed_instance}' (ready: {sorted(devices)})")
    return device
