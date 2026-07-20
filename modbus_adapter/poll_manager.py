"""Polls each group's signals, coalescing contiguous addresses into the fewest Modbus reads, then
decodes, applies change/deadband, and feeds the publisher.

Replaces the OPC UA SubscriptionManager: Modbus has no eventing, so the adapter polls and detects
change client-side. One daemon thread per poll group.
"""
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

from edgecommons.facades.quality import Quality
from edgecommons.facades.util import format_instant

from . import codec
from .config.poll_group import ALWAYS
from .metrics import RESULT_ERROR, RESULT_SUCCESS

LOGGER = logging.getLogger("modbus_adapter.poll")

# Per-request protocol limits.
MAX_READ = {codec.HOLDING: 125, codec.INPUT: 125, codec.COIL: 2000, codec.DISCRETE: 2000}


def _read_timestamp() -> str:
    return format_instant(datetime.now(timezone.utc))


def coalesce(signals, max_gap):
    """Group same-table signals into read blocks, merging spans separated by <= ``max_gap`` and capping
    each block at the protocol max. Returns blocks ``{table, start, end, length, signals[]}``."""
    blocks = []
    by_table = defaultdict(list)
    for s in signals:
        by_table[s.table].append(s)
    for table, sigs in by_table.items():
        cap = MAX_READ[table]
        cur = None
        for s in sorted(sigs, key=lambda x: x.address):
            end = s.address + s.unit_length()
            if cur is not None and s.address <= cur["end"] + max_gap and (end - cur["start"]) <= cap:
                cur["end"] = max(cur["end"], end)
                cur["signals"].append(s)
            else:
                if cur is not None:
                    blocks.append(cur)
                cur = {"table": table, "start": s.address, "end": end, "signals": [s]}
        if cur is not None:
            blocks.append(cur)
    for b in blocks:
        b["length"] = b["end"] - b["start"]
    return blocks


class PollManager:
    def __init__(self, connection, config, publisher, counters, operational_metrics=None,
                 pause_state=None):
        self._conn = connection
        self._config = config
        self._publisher = publisher
        self._counters = counters
        self._operational_metrics = operational_metrics
        self._pause = pause_state
        self._stop = threading.Event()
        self._threads = []
        self._blocks = {}                 # group.id -> coalesced blocks
        self._last = {}                   # (unit_id, name) -> last published value

    def start(self):
        for group in self._config.poll_groups:
            blocks = coalesce(group.signals, group.max_gap)
            self._blocks[group.id] = blocks
            n_signals = sum(len(b["signals"]) for b in blocks)
            LOGGER.info("[%s] poll group '%s': %d signal(s) in %d read block(s) @ %dms",
                        self._config.id, group.id, n_signals, len(blocks), group.poll_interval_ms)
            th = threading.Thread(target=self._run_group, args=(group,),
                                  name=f"poll-{self._config.id}-{group.id}", daemon=True)
            th.start()
            self._threads.append(th)

    def _run_group(self, group):
        interval = group.poll_interval_ms / 1000.0
        while not self._stop.is_set():
            # sb/pause suspends polling + publishing per instance: skip the poll while paused, but
            # keep the loop alive so sb/resume takes effect on the next tick.
            if self._pause is not None and self._pause.is_paused():
                self._stop.wait(interval)
                continue
            t0 = time.monotonic()
            try:
                table_results = self._poll_group(group)
            except Exception as e:  # noqa: BLE001 - keep the loop alive
                LOGGER.error("[%s] poll group '%s' failed: %s", self._config.id, group.id, e)
                table_results = {b["table"]: RESULT_ERROR for b in self._blocks.get(group.id, [])}
            elapsed = time.monotonic() - t0
            if self._counters is not None:
                self._counters.set_poll_latency(elapsed * 1000.0)
            if elapsed > interval and self._operational_metrics is not None:
                for table in {b["table"] for b in self._blocks.get(group.id, [])}:
                    result = table_results.get(table, RESULT_ERROR)
                    self._operational_metrics.record_poll_overrun(group.id, table, result)
            self._stop.wait(max(0.0, interval - elapsed))

    def _poll_group(self, group):
        stats = defaultdict(lambda: {
            "result": RESULT_SUCCESS,
            "pollDurationMs": 0.0,
            "protocolReadRequests": 0,
            "protocolReadErrors": 0,
            "registersRead": 0,
            "signalsDecoded": 0,
            "samplesGood": 0,
            "samplesBad": 0,
            "samplesChanged": 0,
            "samplesSuppressed": 0,
        })
        for block in self._blocks[group.id]:
            table = block["table"]
            block_t0 = time.monotonic()
            try:
                stats[table]["protocolReadRequests"] += 1
                data = self._conn.read(table, block["start"], block["length"], group.unit_id)
                read_ts = _read_timestamp()
                stats[table]["registersRead"] += block["length"]
            except Exception as e:  # noqa: BLE001 - block read failed -> BAD for its signals
                stats[table]["result"] = RESULT_ERROR
                stats[table]["protocolReadErrors"] += 1
                stats[table]["samplesBad"] += len(block["signals"])
                raw = str(e) or "read error"
                for signal in block["signals"]:
                    self._counters.increment_read_error()
                    self._publisher.offer(group, signal, self._publisher.make_sample(None, Quality.BAD, raw))
                stats[table]["pollDurationMs"] += (time.monotonic() - block_t0) * 1000.0
                continue
            for signal in block["signals"]:
                off = signal.address - block["start"]
                slice_ = data[off: off + signal.unit_length()]
                try:
                    value = codec.decode(table, slice_, type_=signal.type, word_order=signal.word_order,
                                         byte_order=signal.byte_order, scale=signal.scale, offset=signal.offset,
                                         count=signal.count, bit=signal.bit)
                except Exception as e:  # noqa: BLE001
                    stats[table]["result"] = RESULT_ERROR
                    stats[table]["samplesBad"] += 1
                    self._counters.increment_read_error()
                    self._publisher.offer(
                        group, signal, self._publisher.make_sample(None, Quality.BAD, str(e), server_ts=read_ts)
                    )
                    continue
                stats[table]["signalsDecoded"] += 1
                stats[table]["samplesGood"] += 1
                self._counters.increment_read()
                # A successful read refreshes this signal for the staleSignals tracker (§5), whether
                # or not the value changed enough to publish — a stable value is not a stale one.
                self._counters.note_signal_update(signal.signal_id(group.unit_id), time.monotonic())
                key = (group.unit_id, signal.name)
                if group.publish_mode == ALWAYS or signal.deadband.exceeds(self._last.get(key), value):
                    self._last[key] = value
                    stats[table]["samplesChanged"] += 1
                    self._publisher.offer(group, signal, self._publisher.make_sample(value, server_ts=read_ts))
                else:
                    stats[table]["samplesSuppressed"] += 1
            stats[table]["pollDurationMs"] += (time.monotonic() - block_t0) * 1000.0
        table_results = {table: values["result"] for table, values in stats.items()}
        if self._operational_metrics is not None:
            for table, values in stats.items():
                result = values.pop("result")
                self._operational_metrics.record_poll(group.id, table, result, **values)
        return table_results

    def poll_once(self):
        """Force one synchronous poll of every group now (the ``repoll`` command's action). Reuses
        the normal poll path, so change/deadband gating and publishing behave exactly as on the
        timer. Returns the number of groups polled."""
        polled = 0
        for group in self._config.poll_groups:
            if group.id not in self._blocks:           # coalesce lazily if start() hasn't run
                self._blocks[group.id] = coalesce(group.signals, group.max_gap)
            try:
                self._poll_group(group)
                polled += 1
            except Exception as e:  # noqa: BLE001 - one bad group must not fail the whole repoll
                LOGGER.error("[%s] repoll of group '%s' failed: %s", self._config.id, group.id, e)
        return polled

    def resolved_signals(self):
        out = []
        for group in self._config.poll_groups:
            for signal in group.signals:
                out.append({"name": signal.name, "unitId": group.unit_id,
                            "signalId": signal.signal_id(group.unit_id), "address": signal.address_dict(group.unit_id)})
        return out

    def stop(self):
        self._stop.set()
        for th in self._threads:
            th.join(timeout=2.0)
