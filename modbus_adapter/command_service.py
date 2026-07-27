"""The on-demand command surface for one device instance: batch write, batch read, browse, and the
status / signals / pause / resume / reconnect / repoll control verbs.

These are served through the library-owned **command inbox** (the
``gg.get_commands()`` facade) rather than per-instance topics: ``main.py`` registers each verb once
at ``CommandScope.INSTANCE``, the inbox resolves the addressing, and
``modbus_adapter.routing.resolve_instance`` picks the addressed device. Each method here returns the
verb result object (which the inbox wraps as ``{"ok": true, "result": ...}``) or raises
:class:`~edgecommons.command_inbox.CommandException` for a coded error reply.

A signal-ref is either ``{"name": "<configured signal>"}`` (friendly, stable) or an explicit
``{"unitId?, table, address, type, ...}`` for arbitrary access — the Modbus analog of OPC UA's
``namespaceUri``-or-``ns`` + ``signalId``.
"""
import logging
import time
from datetime import datetime, timezone

from edgecommons.command_inbox import CommandException

from . import codec
from .config.signal_spec import SignalSpec
from .instance_state import device_state
from .metrics import RESULT_ERROR, RESULT_SUCCESS

LOGGER = logging.getLogger("modbus_adapter.command")

#: Default page size for ``sb/browse``.
BROWSE_DEFAULT_MAX = 200
#: Hard cap on an ``sb/browse`` page.
BROWSE_MAX = 1000


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def panels():
    """The three edge-console panel descriptors (SOUTHBOUND.md §6), each ``scope: "instance"`` with
    ``order`` 10/20/30, bound to the verbs this adapter serves. Core validates ``id``/``title``/
    uniqueness; the widget kinds and bound verbs are console-interpreted, so they ride verbatim.
    Every command-backed widget repeats ``scope: "instance"``, which the console renderer requires.
    No widget names a ``writeVerb``: writes stay on the command surface behind the allow-list."""
    return [
        {
            "id": "overview", "title": "Overview", "order": 10, "scope": "instance",
            "widgets": [
                {
                    "kind": "summary", "id": "overview-summary", "title": "Adapter overview",
                    "rows": [
                        {"label": "Signals", "value": "Configured register map via cmd/sb/signals"},
                        {"label": "Lifecycle", "value": "Pause, resume, reconnect, and repoll the instance"},
                        {"label": "Writes", "value": "Allow-listed via writes.allow[]; checked before device I/O"},
                    ],
                },
                {
                    "kind": "commandSummary", "id": "overview-lifecycle", "title": "Lifecycle bindings",
                    "verbs": ["sb/status", "reconnect", "sb/pause", "sb/resume", "repoll"],
                },
            ],
            "verbs": ["sb/status", "reconnect", "sb/pause", "sb/resume"],
        },
        {
            "id": "signals", "title": "Signals", "order": 20, "scope": "instance",
            "widgets": [
                {
                    "kind": "signalGrid", "id": "configured-signals", "title": "Configured signals",
                    "scope": "instance",
                    "signalsVerb": "sb/signals",
                    # Descriptor-compat hint: the shipped edge-console signalGrid reads
                    # `subscriptionsVerb`. Point that key at the `sb/signals` verb too, so the
                    # current console binds correctly until it reads `signalsVerb`. This is a
                    # descriptor field alias, NOT a wire-verb alias — no `sb/subscriptions` verb
                    # exists.
                    "subscriptionsVerb": "sb/signals",
                    "readVerb": "sb/read",
                },
            ],
            "verbs": ["sb/signals", "sb/read", "sb/write", "repoll"],
        },
        {
            "id": "diagnostics", "title": "Diagnostics", "order": 30, "scope": "instance",
            "widgets": [
                {
                    "kind": "treeBrowser", "id": "inventory-tree", "title": "Register map",
                    "scope": "instance", "mode": "hierarchical", "rootRef": "root",
                    "depth": 1, "maxRefs": 200,
                    "browseVerb": "sb/browse", "readVerb": "sb/read",
                },
                {"kind": "keyValueList", "id": "status-detail", "title": "Status detail"},
            ],
            "verbs": ["sb/browse", "sb/status"],
        },
    ]


class CommandService:
    def __init__(self, connection, events, config, counters, poller, operational_metrics=None,
                 pause_state=None):
        self._conn = connection
        self._events = events                # EventEmitter (evt/write audit records)
        self._config = config
        self._counters = counters
        self._poller = poller
        self._operational_metrics = operational_metrics
        self._pause = pause_state
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
        t0 = time.monotonic()
        result = RESULT_ERROR
        read_signals = 0
        reads = []
        try:
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
                # Capture time (four-slot timestamp model): serverTs is stamped the moment the
                # register read completes -- the same stamp the poll path uses -- not when the
                # reply is assembled.
                server_ts = _now_iso()
                self._counters.increment_read()
                reads.append({"signal": signal_obj, "value": value, "quality": quality,
                              "qualityRaw": raw, "sourceTs": None, "serverTs": server_ts})
            read_signals = len(reads)
            result = RESULT_SUCCESS
            return {"id": self._config.id, "reads": reads}
        finally:
            self._record_command("sb/read", result, t0, readSignals=read_signals)

    def write(self, body):
        """``sb/write`` — batch write (mutating). Body ``{instance?, writes:[{name|table+address, value}]}``.

        Every entry is gated by the instance's ``writes.allow[]`` allow-list — matched on the stable
        ``signal.id``, **before any device I/O** (SOUTHBOUND.md §2.2 / D-U16): a signal not on the
        list is refused without ever touching the device. Standardized error codes: an all-refused
        batch raises ``WRITE_NOT_ALLOWED``; an all-failed batch of allowed writes raises
        ``WRITE_FAILED``. Each attempted write is audited on ``evt/write``."""
        t0 = time.monotonic()
        result = RESULT_ERROR
        writes = body.get("writes") if "writes" in body else ([body] if body else [])
        results = []
        refused = 0
        attempted = 0
        succeeded = 0
        try:
            for w in writes:
                w = w or {}
                name = w.get("name")
                try:
                    signal, unit = self._resolve(w)
                except ValueError as e:
                    results.append({"signal": name, "ok": False, "error": str(e)})
                    continue
                # THE ALLOW-LIST — checked here, on the stable signal.id, BEFORE any device I/O.
                signal_id = signal.signal_id(unit)
                if not self._config.permits(signal_id):
                    refused += 1
                    results.append({"signal": signal.name, "ok": False,
                                    "error": f"'{signal_id}' is not in this instance's writes.allow"})
                    continue
                if "value" not in w:
                    results.append({"signal": signal.name, "ok": False, "error": "missing 'value'"})
                    continue
                if signal.table not in codec.WRITABLE_TABLES:
                    results.append({"signal": signal.name, "ok": False,
                                    "error": f"table '{signal.table}' is read-only"})
                    continue
                if signal.type == "bool" and signal.bit is not None:
                    results.append({"signal": signal.name, "ok": False,
                                    "error": "bit writes (read-modify-write) not supported"})
                    continue
                attempted += 1
                ok, error = self._write_one(signal, unit, w["value"])
                if ok:
                    succeeded += 1
                results.append({"signal": signal.name, "value": w["value"], "ok": ok,
                                **({"error": error} if error else {})})
                self._events.write(ok, signal.name, w["value"], error)
            # WRITE_NOT_ALLOWED only when EVERY entry was an allow-list refusal (nothing else tried).
            if writes and refused == len(writes):
                raise CommandException("WRITE_NOT_ALLOWED",
                                       "no entry is in this instance's writes.allow list")
            # WRITE_FAILED when every allowed write reached the device and every one failed.
            if attempted > 0 and succeeded == 0:
                raise CommandException("WRITE_FAILED", "every attempted write was rejected by the device")
            result = RESULT_SUCCESS
            return {"id": self._config.id, "written": succeeded, "results": results}
        finally:
            self._record_command(
                "sb/write",
                result,
                t0,
                writeSignals=len(writes),
                writeFailures=sum(1 for r in results if not r["ok"]),
            )

    def _write_one(self, signal, unit, value):
        try:
            enc = codec.encode(signal.table, value, type_=signal.type, word_order=signal.word_order,
                               byte_order=signal.byte_order, scale=signal.scale, offset=signal.offset,
                               count=signal.count)
        except Exception as e:  # noqa: BLE001 - a caller-side encode error, not a device failure
            LOGGER.error("[%s] write to %s failed to encode: %s", self._config.id, signal.name, e)
            return False, (str(e) or "encode error")
        try:
            if signal.table == codec.COIL:
                self._conn.write_coil(signal.address, enc, unit)
            else:
                self._conn.write_registers(signal.address, enc, unit)
            self._counters.increment_write()
            return True, None
        except Exception as e:  # noqa: BLE001
            # A DEVICE-PATH failure: the entry passed validation + the allow-list and the device
            # rejected it (or the link died) — the only failures southbound_health.writeErrors counts.
            self._counters.increment_write_error()
            LOGGER.error("[%s] write to %s failed: %s", self._config.id, signal.name, e)
            return False, (str(e) or "write error")

    def status(self):
        """``sb/status`` — instance state, connection flag, paused flag, and read/write counters.

        ``state`` is the single state model (D-SC-7): the very token the ``state`` keepalive's
        ``instances[]`` carries for this instance, so the pulled answer and the pushed one can
        never disagree."""
        t0 = time.monotonic()
        result = RESULT_ERROR
        try:
            ret = {"id": self._config.id, "state": self.state(),
                   "connected": self._conn.is_connected(),
                   "paused": self.is_paused(), "metrics": self._counters.to_dict()}
            result = RESULT_SUCCESS
            return ret
        finally:
            self._record_command("sb/status", result, t0)

    def state(self) -> str:
        """This instance's state token (``ONLINE``/``PAUSED``/``BACKOFF``) — the one model both
        ``sb/status`` and the keepalive's ``instances[]`` read (D-SC-7). Link truth wins: a paused
        instance whose link is down reads ``BACKOFF``, the pause still reported in ``paused``."""
        return device_state(self.is_paused(), self._conn.is_connected())

    def is_paused(self) -> bool:
        return self._pause is not None and self._pause.is_paused()

    def pause(self):
        """``sb/pause`` — suspend polling/publishing for this instance. Confirmed + idempotent:
        the reply is ``{paused: true, changed}``, ``changed`` false when already paused."""
        t0 = time.monotonic()
        result = RESULT_ERROR
        try:
            changed = self._pause.set(True) if self._pause is not None else False
            result = RESULT_SUCCESS
            return {"id": self._config.id, "paused": True, "changed": changed}
        finally:
            self._record_command("sb/pause", result, t0)

    def resume(self):
        """``sb/resume`` — resume a paused instance. Confirmed + idempotent: the reply is
        ``{paused: false, changed}``, ``changed`` false when already running."""
        t0 = time.monotonic()
        result = RESULT_ERROR
        try:
            changed = self._pause.set(False) if self._pause is not None else False
            result = RESULT_SUCCESS
            return {"id": self._config.id, "paused": False, "changed": changed}
        finally:
            self._record_command("sb/resume", result, t0)

    def browse(self, body):
        """``sb/browse`` — a walk of the configured signal inventory, in two mutually exclusive
        request forms. Modbus has no address-space discovery (signals are declared explicitly), so
        both forms serve the *configured* inventory.

        **Paged** (the default): body ``{instance?, cursor?, max?}`` →
        ``{id, entries:[{id, name, type}], cursor?}``. ``cursor`` is an opaque offset token; it is
        present in the reply only while more pages remain. Distinct from ``sb/signals``, which
        returns the whole inventory in one shot.

        **Hierarchical** (the ``treeBrowser`` panel mode, selected by the presence of ``ref``): body
        ``{instance?, ref, depth?, maxRefs?}`` → ``{id, mode, root, refCount, depth, truncated}``.
        ``depth``/``maxRefs`` are clamped to 1..4 / 1..1000. Mixing ``ref``/``depth``/``maxRefs``
        with ``cursor``/``max``, or sending ``depth``/``maxRefs`` without ``ref``, is ``BAD_ARGS``."""
        t0 = time.monotonic()
        result = RESULT_ERROR
        try:
            # The two request forms are mutually exclusive: `ref`/`depth`/`maxRefs` select the
            # hierarchical panel mode, `cursor`/`max` the paged one — and the hierarchical-only
            # arguments are meaningless without a `ref`.
            hierarchical_keys = any(k in body for k in ("ref", "depth", "maxRefs"))
            if hierarchical_keys and any(k in body for k in ("cursor", "max")):
                raise CommandException(
                    "BAD_ARGS",
                    "`ref`/`depth`/`maxRefs` (hierarchical) and `cursor`/`max` (paged) are mutually exclusive")
            if hierarchical_keys and "ref" not in body:
                raise CommandException(
                    "BAD_ARGS", "`depth`/`maxRefs` are hierarchical-mode arguments and require `ref`")
            if "ref" in body:
                out = self._browse_hierarchical(body)
                result = RESULT_SUCCESS
                return out
            signals = self._poller.resolved_signals()
            cursor = body.get("cursor")
            try:
                start = int(cursor) if cursor is not None else 0
            except (TypeError, ValueError):
                raise CommandException("BAD_ARGS", f"invalid cursor {cursor!r} (expected an offset token)")
            if start < 0:
                raise CommandException("BAD_ARGS", f"invalid cursor {cursor!r} (must be >= 0)")
            requested = body.get("max")
            max_entries = int(requested) if isinstance(requested, int) and not isinstance(requested, bool) \
                else BROWSE_DEFAULT_MAX
            max_entries = max(1, min(BROWSE_MAX, max_entries))
            page = signals[start:start + max_entries]
            entries = [{"id": s["signalId"], "name": s["name"],
                        "type": (s.get("address") or {}).get("type")} for s in page]
            out = {"id": self._config.id, "entries": entries}
            nxt = start + max_entries
            if nxt < len(signals):
                out["cursor"] = str(nxt)
            result = RESULT_SUCCESS
            return out
        finally:
            self._record_command("sb/browse", result, t0)

    def _browse_hierarchical(self, body):
        """The ``treeBrowser`` panel mode of ``sb/browse``: ``ref`` names a node in the **same**
        configured inventory the paged mode serves. ``"root"`` is the device node, whose
        ``contains`` refs are the inventory (bounded by ``maxRefs``); a signal id is a known leaf
        (``"refs": []``); an unknown ref is ``BAD_ARGS``. The Modbus inventory is flat, so a deeper
        ``depth`` finds no grandchildren — it is still validated, clamped, and echoed."""
        ref = body.get("ref")
        if not isinstance(ref, str) or not ref:
            raise CommandException("BAD_ARGS", "`ref` must be a non-empty string")
        depth = body.get("depth")
        depth = max(1, min(4, int(depth) if isinstance(depth, int) and not isinstance(depth, bool) else 1))
        max_refs = body.get("maxRefs")
        max_refs = max(1, min(1000, int(max_refs)
                              if isinstance(max_refs, int) and not isinstance(max_refs, bool) else 200))

        signals = self._poller.resolved_signals()
        if ref == "root":
            refs = [{
                "referenceType": "contains",
                "target": {"nodeId": s["signalId"], "name": s["name"], "nodeClass": "signal",
                           "dataType": (s.get("address") or {}).get("type")},
            } for s in signals[:max_refs]]
            return {
                "id": self._config.id,
                "mode": "hierarchical",
                "root": {"nodeId": "root", "name": self._config.id, "nodeClass": "device",
                         "dataType": None, "refs": refs},
                "refCount": len(refs),
                "depth": depth,
                "truncated": len(signals) > max_refs,
            }
        node = next((s for s in signals if s["signalId"] == ref), None)
        if node is None:
            raise CommandException("BAD_ARGS", f"unknown browse ref {ref!r}")
        return {
            "id": self._config.id,
            "mode": "hierarchical",
            "root": {"nodeId": node["signalId"], "name": node["name"], "nodeClass": "signal",
                     "dataType": (node.get("address") or {}).get("type"), "refs": []},
            "refCount": 0,
            "depth": depth,
            "truncated": False,
        }

    def signals(self):
        """``sb/signals`` — the configured/polled point list (so the console needs no static config)."""
        t0 = time.monotonic()
        result = RESULT_ERROR
        try:
            ret = {"id": self._config.id, "signals": self._poller.resolved_signals()}
            result = RESULT_SUCCESS
            return ret
        finally:
            self._record_command("sb/signals", result, t0)

    def reconnect(self):
        """``reconnect`` — drop + re-establish the Modbus link (one bounded attempt)."""
        t0 = time.monotonic()
        result = RESULT_ERROR
        try:
            try:
                self._conn.reconnect()
            except Exception as e:  # noqa: BLE001
                raise CommandException("RECONNECT_FAILED", str(e) or "reconnect failed")
            ret = {"id": self._config.id, "connected": self._conn.is_connected()}
            result = RESULT_SUCCESS
            return ret
        finally:
            self._record_command("reconnect", result, t0, reconnectRequests=1)

    def repoll(self):
        """``repoll`` — force an immediate poll cycle now instead of waiting for the interval.
        Refused while the instance is paused (``PAUSED``) — a paused instance publishes nothing."""
        t0 = time.monotonic()
        result = RESULT_ERROR
        try:
            if self.is_paused():
                raise CommandException("PAUSED", "instance is paused — resume before repolling")
            published = self._poller.poll_once()
            result = RESULT_SUCCESS
            return {"id": self._config.id, "polled": published}
        finally:
            self._record_command("repoll", result, t0, repollRequests=1)

    def _record_command(self, verb, result, started, **values):
        if self._operational_metrics is None:
            return
        self._operational_metrics.record_command(
            verb,
            result,
            commandLatencyMs=(time.monotonic() - started) * 1000.0,
            **values,
        )
