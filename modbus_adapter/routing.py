"""Instance routing for the shared command inbox (SOUTHBOUND.md §2.2 / D-U28).

The library command inbox subscribes **both** command scopes on the primary connection —
``ecv1/{device}/modbus-adapter/cmd/#`` (component scope) and
``ecv1/{device}/modbus-adapter/+/cmd/#`` (instance scope) — and hands handlers registered through
``register_scoped`` the topic-addressed instance token beside the request (``None`` for a
component-scoped delivery). :func:`resolve_instance` turns that token plus the request body's
optional ``instance`` selector into the one addressed :class:`~modbus_adapter.device.ModbusDevice`:

- The **topic-addressed instance is authoritative**: a body ``instance`` that disagrees with it is
  refused with ``BAD_ARGS``; when only the topic token is present, it routes.
- A **component-scoped** delivery routes by the body ``instance`` selector, which is optional iff
  exactly one device is configured (missing on a multi-device adapter is ``BAD_ARGS``).
- Either way, a selector that names no ready device is ``NO_SUCH_INSTANCE``.
"""
from edgecommons.command_inbox import CommandException


def resolve_instance(devices, body, addressed_instance):
    """Pick the target device for one command delivery.

    :param devices: the ready devices, ``{instance_id: ModbusDevice}``
    :param body: the request body (a dict; its ``instance`` key is the body selector)
    :param addressed_instance: the topic-addressed instance token, or ``None`` for a
        component-scoped delivery
    :returns: the addressed device
    :raises CommandException: ``BAD_ARGS`` on a conflicting or missing selector,
        ``NO_SUCH_INSTANCE`` on an unknown one
    """
    body_instance = body.get("instance")
    if addressed_instance is not None:
        if body_instance is not None and body_instance != addressed_instance:
            raise CommandException(
                "BAD_ARGS",
                f"body instance '{body_instance}' conflicts with the topic-addressed"
                f" instance '{addressed_instance}'")
        selector = addressed_instance
    else:
        selector = body_instance
    if selector is None:
        if len(devices) == 1:
            return next(iter(devices.values()))
        raise CommandException(
            "BAD_ARGS", f"body must specify 'instance' (configured: {sorted(devices)})")
    device = devices.get(selector)
    if device is None:
        raise CommandException(
            "NO_SUCH_INSTANCE", f"no ready device instance '{selector}' (ready: {sorted(devices)})")
    return device
