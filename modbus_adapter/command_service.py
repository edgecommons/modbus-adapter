"""The on-demand command surface for one device instance: batch write, batch read, and the
status / signals / reconnect / repoll control queries.

Since the UNS migration these are served through the library-owned **command inbox** (the
``gg.get_commands()`` facade) rather than per-instance legacy topics: ``main.py`` registers the verbs
once on the shared ``main``-instance inbox (``ecv1/{device}/ModbusAdapter/main/cmd/#``) and dispatches
each into the right device by the request body's ``instance`` selector. Each method here returns the
verb result object (which the inbox wraps as ``{"ok": true, "result": ...}``) or raises
:class:`~ggcommons.command_inbox.CommandException` for a coded error reply.

A signal-ref is either ``{"name": "<configured signal>"}`` (friendly, stable) or an explicit
``{"unitId?, table, address, type, ...}`` for arbitrary access — the Modbus analog of OPC UA's
``namespaceUri``-or-``ns`` + ``signalId``.
"""
import logging
from datetime import datetime, timezone

from ggcommons.command_inbox import CommandException

from . import codec
from .config.signal_spec import SignalSpec

LOGGER = logging.getLogger("modbus_adapter.command")


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CommandService:
    def __init__(self, connection, events, config, counters, poller):
        self._conn = connection
        self._events = events                # EventEmitter (evt/write audit records)
        self._config = config
        self._counters = counters
        self._poller = poller
        self._by_name = {s.name: (g, s) for (g, s) in config.all_signals()}

    # --- resolution -------------------------------------------------------------------------
    def _resolve(self, ref):
        """Return (SignalSpec, unit_id) for a signal-ref, or raise ValueError if unresolvable."""
        name = ref.get("name")
        if name and name in self._by_name:
            group, signal = self._by_name[name]
            return signal, group.unit_id
        if "table" in ref and "address" in ref:
            unit = int(ref.get("unitId", self._conn.conn.unit_id))
            spec = dict(ref)
            spec.setdefault("name", f"{ref['table']}:{ref['address']}")
            return SignalSpec.from_dict(spec), unit
        raise ValueError(f"unresolvable signal-ref (need a known 'name' or explicit table+address): {ref}")

    def _read_one(self, signal, unit):
        data = self._conn.read(signal.table, signal.address, signal.unit_length(), unit)
        return codec.decode(signal.table, data, type_=signal.type, word_order=signal.word_order,
                            byte_order=signal.byte_order, scale=signal.scale, offset=signal.offset,
                            count=signal.count, bit=signal.bit)

    # --- verb handlers (return the result object; raise CommandException on a coded error) ---
    def read(self, body):
        """``sb/read`` — on-demand batch read. Body ``{instance?, signals:[<ref>]}``."""
        reads = []
        for ref in (body.get("signals") or []):
            try:
                signal, unit = self._resolve(ref)
            except ValueError as e:
                LOGGER.warning("[%s] read signal skipped: %s", self._config.id, e)
                continue
            signal_obj = {"id": signal.signal_id(unit), "address": signal.address_dict(unit)}
            try:
                value, quality, raw = self._read_one(signal, unit), "GOOD", "Good"
            except Exception as e:  # noqa: BLE001
                value, quality, raw = None, "BAD", (str(e) or "read error")
                self._counters.increment_read_error()
            self._counters.increment_read()
            reads.append({"signal": signal_obj, "value": value, "quality": quality,
                          "qualityRaw": raw, "sourceTs": None, "serverTs": _now_iso()})
        return {"id": self._config.id, "reads": reads}

    def write(self, body):
        """``sb/write`` — batch write (mutating). Body ``{instance?, writes:[{name|table+address, value}]}``.
        Gated on ``write.enabled``; each entry is audited on ``evt/write``."""
        if not self._config.write_enabled:
            raise CommandException("WRITE_DISABLED",
                                   f"writes are disabled for instance '{self._config.id}' "
                                   "(set write.enabled: true in its config)")
        writes = body.get("writes") if "writes" in body else ([body] if body else [])
        results = []
        for w in writes:
            name = (w or {}).get("name")
            if "value" not in w:
                results.append({"signal": name, "ok": False, "error": "missing 'value'"})
                continue
            try:
                signal, unit = self._resolve(w)
            except ValueError as e:
                results.append({"signal": name, "ok": False, "error": str(e)})
                continue
            if signal.table not in codec.WRITABLE_TABLES:
                results.append({"signal": signal.name, "ok": False,
                                "error": f"table '{signal.table}' is read-only"})
                continue
            if signal.type == "bool" and signal.bit is not None:
                results.append({"signal": signal.name, "ok": False,
                                "error": "bit writes (read-modify-write) not supported"})
                continue
            ok, error = self._write_one(signal, unit, w["value"])
            results.append({"signal": signal.name, "value": w["value"], "ok": ok,
                            **({"error": error} if error else {})})
            self._events.write(ok, signal.name, w["value"], error)
        return {"id": self._config.id, "written": sum(1 for r in results if r["ok"]), "results": results}

    def _write_one(self, signal, unit, value):
        try:
            enc = codec.encode(signal.table, value, type_=signal.type, word_order=signal.word_order,
                               byte_order=signal.byte_order, scale=signal.scale, offset=signal.offset,
                               count=signal.count)
            if signal.table == codec.COIL:
                self._conn.write_coil(signal.address, enc, unit)
            else:
                self._conn.write_registers(signal.address, enc, unit)
            self._counters.increment_write()
            return True, None
        except Exception as e:  # noqa: BLE001
            LOGGER.error("[%s] write to %s failed: %s", self._config.id, signal.name, e)
            return False, (str(e) or "write error")

    def status(self):
        """``sb/status`` — connection state + read/write counters."""
        return {"id": self._config.id, "connected": self._conn.is_connected(),
                "metrics": self._counters.to_dict()}

    def signals(self):
        """``sb/signals`` — the configured/polled point list (so the console needs no static config)."""
        return {"id": self._config.id, "signals": self._poller.resolved_signals()}

    def reconnect(self):
        """``reconnect`` — drop + re-establish the Modbus link (one bounded attempt)."""
        try:
            self._conn.reconnect()
        except Exception as e:  # noqa: BLE001
            raise CommandException("RECONNECT_FAILED", str(e) or "reconnect failed")
        return {"id": self._config.id, "connected": self._conn.is_connected()}

    def repoll(self):
        """``repoll`` — force an immediate poll cycle now instead of waiting for the interval."""
        published = self._poller.poll_once()
        return {"id": self._config.id, "polled": published}
