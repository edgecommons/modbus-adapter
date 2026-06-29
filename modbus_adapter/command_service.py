"""The command surface over messaging: batch write, on-demand batch read (request/reply), and the
status / tags control queries. Mirrors the OPC UA CommandService.

A tag-ref is either ``{"name": "<configured tag>"}`` (friendly, stable) or an explicit
``{"unitId?, table, address, type, ...}`` for arbitrary access — the Modbus analog of OPC UA's
``namespaceUri``-or-``ns`` + ``tagId``.
"""
import logging
from datetime import datetime, timezone

from ggcommons.messaging.message_builder import MessageBuilder

from . import codec
from .config.tag_spec import TagSpec

LOGGER = logging.getLogger("modbus_adapter.command")


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CommandService:
    def __init__(self, connection, messaging, config_manager, config, counters, poller):
        self._conn = connection
        self._messaging = messaging
        self._cm = config_manager
        self._config = config
        self._counters = counters
        self._poller = poller
        self._by_name = {t.name: (g, t) for (g, t) in config.all_tags()}

    def subscribe(self):
        if self._config.write_enabled:
            self._messaging.subscribe(self._config.write_topic, self._handle_write)
            LOGGER.info("[%s] write enabled on %s", self._config.id, self._config.write_topic)
        self._messaging.subscribe(self._config.read_topic, self._handle_read)
        self._messaging.subscribe(self._config.control_topic, self._handle_control)

    # --- resolution -------------------------------------------------------------------------
    def _resolve(self, ref):
        """Return (TagSpec, unit_id) for a tag-ref, or raise ValueError if unresolvable."""
        name = ref.get("name")
        if name and name in self._by_name:
            group, tag = self._by_name[name]
            return tag, group.unit_id
        if "table" in ref and "address" in ref:
            unit = int(ref.get("unitId", self._conn.conn.unit_id))
            spec = dict(ref)
            spec.setdefault("name", f"{ref['table']}:{ref['address']}")
            return TagSpec.from_dict(spec), unit
        raise ValueError(f"unresolvable tag-ref (need a known 'name' or explicit table+address): {ref}")

    def _read_one(self, tag, unit):
        data = self._conn.read(tag.table, tag.address, tag.unit_length(), unit)
        return codec.decode(tag.table, data, type_=tag.type, word_order=tag.word_order,
                            byte_order=tag.byte_order, scale=tag.scale, offset=tag.offset,
                            count=tag.count, bit=tag.bit)

    # --- handlers ---------------------------------------------------------------------------
    def _handle_read(self, topic, request):
        try:
            body = _body(request)
            reads = []
            for ref in body.get("tags", []):
                try:
                    tag, unit = self._resolve(ref)
                except ValueError as e:
                    LOGGER.warning("[%s] read tag skipped: %s", self._config.id, e)
                    continue
                tag_obj = {"id": tag.tag_id(unit), "address": tag.address_dict(unit)}
                try:
                    value, quality, raw = self._read_one(tag, unit), "GOOD", "Good"
                except Exception as e:  # noqa: BLE001
                    value, quality, raw = None, "BAD", (str(e) or "read error")
                    self._counters.increment_read_error()
                self._counters.increment_read()
                reads.append({"tag": tag_obj, "value": value, "quality": quality,
                              "qualityRaw": raw, "sourceTs": None, "serverTs": _now_iso()})
            self._reply(request, "SouthboundReadResult", {"id": self._config.id, "reads": reads})
        except Exception as e:  # noqa: BLE001
            LOGGER.error("[%s] read request failed: %s", self._config.id, e)

    def _handle_write(self, topic, request):
        try:
            body = _body(request)
            writes = body.get("writes") if "writes" in body else ([body] if body else [])
            for w in writes:
                if "value" not in w:
                    LOGGER.warning("[%s] write entry missing 'value'; skipping: %s", self._config.id, w)
                    continue
                try:
                    tag, unit = self._resolve(w)
                except ValueError as e:
                    LOGGER.warning("[%s] write entry skipped: %s", self._config.id, e)
                    continue
                if tag.table not in codec.WRITABLE_TABLES:
                    LOGGER.warning("[%s] table '%s' is read-only; skipping %s", self._config.id, tag.table, tag.name)
                    continue
                if tag.type == "bool" and tag.bit is not None:
                    LOGGER.warning("[%s] bit writes (read-modify-write) not supported; skipping %s",
                                   self._config.id, tag.name)
                    continue
                try:
                    enc = codec.encode(tag.table, w["value"], type_=tag.type, word_order=tag.word_order,
                                       byte_order=tag.byte_order, scale=tag.scale, offset=tag.offset,
                                       count=tag.count)
                    if tag.table == codec.COIL:
                        self._conn.write_coil(tag.address, enc, unit)
                    else:
                        self._conn.write_registers(tag.address, enc, unit)
                    self._counters.increment_write()
                except Exception as e:  # noqa: BLE001
                    LOGGER.error("[%s] write to %s failed: %s", self._config.id, tag.name, e)
        except Exception as e:  # noqa: BLE001
            LOGGER.error("[%s] write request failed: %s", self._config.id, e)

    def _handle_control(self, topic, request):
        if topic.endswith("status"):
            self._reply(request, "status", {"id": self._config.id,
                        "connected": self._conn.is_connected(), "metrics": self._counters.to_dict()})
        elif topic.endswith("tags") or topic.endswith("subscriptions"):
            self._reply(request, "tags", {"id": self._config.id, "tags": self._poller.resolved_tags()})

    def _reply(self, request, name, payload):
        reply = (MessageBuilder.create(name, "1.0")
                 .with_correlation_id(request.get_correlation_id())
                 .with_payload(payload).with_config(self._cm).build())
        self._messaging.reply(request, reply)


def _body(message) -> dict:
    b = message.get_body()
    return b if isinstance(b, dict) else {}
