"""Publishes Modbus operator events through the instance's ``events()`` facade
(``docs/platform/DESIGN-class-facades.md`` §2.2, ``edgecommons.facades.events_facade.EventsFacade``) --
the facade **derives** the ``evt/{severity}/{type}`` channel from the event's own severity + type, so
the channel and body can never disagree. This closes the drift DESIGN-class-facades §1.2 documents:
the pre-migration ``evt/connection``/``evt/write`` channels carried **no severity segment at all**,
unlike the OPC UA adapter's (partially) severity-prefixed channels -- after this migration both
adapters emit the same ``evt/{severity}/{type}`` shape.

The poll loop's telemetry (``data``) and the ``southbound_health`` metric already report steady-state
value/quality/counters; an *event* is the discrete, timestamped thing a console raises an alert on the
instant it happens:

- ``connection`` -- a Modbus link up/down transition (device power-cycled / network drop). Modeled as
  a stateful alarm (:meth:`~edgecommons.facades.events_facade.EventsFacade.raise_alarm` /
  :meth:`~edgecommons.facades.events_facade.EventsFacade.clear_alarm`, default severity ``critical``) so
  the drop and the restore ride the *same* ``evt/critical/connection`` channel -- a console tracking
  ``evt/critical/#`` sees both ends of the same alarm (this subsumes OPC UA's
  ``connection-lost``/``connection-restored`` pair, DESIGN-class-facades §2.2).
- ``write``      -- a per-write audit record (which signal, value, ok/fail) for command-review, an
  :meth:`~edgecommons.facades.events_facade.EventsFacade.emit` -- ``info`` on success, ``warning`` on
  failure (not an alarm: a single write attempt is not a standing condition).

Emission is best-effort -- an event must never break polling or a command reply, so every publish here
is caught and logged rather than propagated (the facade itself only ever raises on a missing
``type``, never on a transport failure).
"""
import logging
from typing import Any, Dict, Optional

from edgecommons.facades.severity import Severity

LOGGER = logging.getLogger("modbus_adapter.events")


class EventEmitter:
    """Publishes ``evt``-class events for one device instance through its ``events()`` facade
    (``gg.instance(id).events()``)."""

    def __init__(self, events_facade):
        self._events = events_facade          # this instance's EventsFacade

    def connection(self, connected: bool, context: Optional[Dict[str, Any]] = None) -> None:
        """A Modbus link up/down transition -- a stateful alarm on ``evt/critical/connection``:
        raised when the link drops, cleared when it's restored (or established at startup). Never
        raises."""
        try:
            if connected:
                self._events.clear_alarm("connection", context)
            else:
                self._events.raise_alarm("connection", "Modbus link down", context)
        except Exception as e:  # noqa: BLE001 - an event must never break the caller
            LOGGER.debug("connection event emit failed: %s", e)

    def write(self, ok: bool, signal_name: str, value: Any, error: Optional[str] = None) -> None:
        """A per-write audit record -- ``evt/info/write`` on success, ``evt/warning/write`` on
        failure. Never raises."""
        context: Dict[str, Any] = {"signal": signal_name, "value": value}
        if error:
            context["error"] = error
        message = f"write to '{signal_name}' {'succeeded' if ok else 'failed'}"
        try:
            self._events.emit("write", message, context,
                              severity=Severity.INFO if ok else Severity.WARNING)
        except Exception as e:  # noqa: BLE001 - an event must never break the caller
            LOGGER.debug("write event emit failed: %s", e)
