"""Discrete UNS ``evt``-class events for the edge console (docs/SOUTHBOUND.md; UNS ``evt`` class).

The poll loop's telemetry (``data``) and the ``southbound_health`` metric already report steady-state
value/quality/counters; an *event* is the discrete, timestamped thing a console raises an alert on the
instant it happens:

- ``evt/connection`` — a Modbus link up/down transition (device power-cycled / network drop), surfaced
  immediately instead of waiting for the next ``connectionState`` metric tick.
- ``evt/write``      — a per-write audit record (which signal, value, ok/fail) for command-review.

Topics are minted through this instance's UNS builder (``ecv1/{device}/{component}/{instance}/evt/{channel}``);
messages are built through the instance-scoped handle, so the top-level ``identity`` is stamped
automatically. Emission is best-effort — an event must never break polling or a command reply.
"""
import logging

from ggcommons.uns import UnsClass

LOGGER = logging.getLogger("modbus_adapter.events")


class EventEmitter:
    """Publishes ``evt``-class events for one device instance."""

    def __init__(self, messaging, instance):
        self._messaging = messaging          # MessagingClient (static surface)
        self._instance = instance            # GgInstance handle (gg.instance(config.id))

    def emit(self, channel: str, body: dict) -> None:
        """Publish one event on ``evt/{channel}``. Never raises."""
        try:
            topic = self._instance.uns().topic(UnsClass.EVT, channel)
            msg = self._instance.new_message("SouthboundEvent", "1.0").with_payload(body).build()
            self._messaging.publish(topic, msg)
        except Exception as e:  # noqa: BLE001 - an event must never break the caller
            LOGGER.debug("event emit to evt/%s failed: %s", channel, e)
