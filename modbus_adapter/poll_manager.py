"""Polls each group's signals, coalescing contiguous addresses into the fewest Modbus reads, then
decodes, applies change/deadband, and feeds the publisher.

Replaces the OPC UA SubscriptionManager: Modbus has no eventing, so the adapter polls and detects
change client-side. One daemon thread per poll group.
"""
import logging
import threading
import time
from collections import defaultdict

from . import codec
from .config.poll_group import ALWAYS

LOGGER = logging.getLogger("modbus_adapter.poll")

# Per-request protocol limits.
MAX_READ = {codec.HOLDING: 125, codec.INPUT: 125, codec.COIL: 2000, codec.DISCRETE: 2000}


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
    def __init__(self, connection, config, publisher, counters):
        self._conn = connection
        self._config = config
        self._publisher = publisher
        self._counters = counters
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
            t0 = time.monotonic()
            try:
                self._poll_group(group)
            except Exception as e:  # noqa: BLE001 - keep the loop alive
                LOGGER.error("[%s] poll group '%s' failed: %s", self._config.id, group.id, e)
            self._stop.wait(max(0.0, interval - (time.monotonic() - t0)))

    def _poll_group(self, group):
        for block in self._blocks[group.id]:
            try:
                data = self._conn.read(block["table"], block["start"], block["length"], group.unit_id)
            except Exception as e:  # noqa: BLE001 - block read failed -> BAD for its signals
                raw = str(e) or "read error"
                for signal in block["signals"]:
                    self._counters.increment_read_error()
                    self._publisher.offer(group, signal, self._publisher.make_sample(None, "BAD", raw))
                continue
            for signal in block["signals"]:
                off = signal.address - block["start"]
                slice_ = data[off: off + signal.unit_length()]
                try:
                    value = codec.decode(signal.table, slice_, type_=signal.type, word_order=signal.word_order,
                                         byte_order=signal.byte_order, scale=signal.scale, offset=signal.offset,
                                         count=signal.count, bit=signal.bit)
                except Exception as e:  # noqa: BLE001
                    self._counters.increment_read_error()
                    self._publisher.offer(group, signal, self._publisher.make_sample(None, "BAD", str(e)))
                    continue
                self._counters.increment_read()
                key = (group.unit_id, signal.name)
                if group.publish_mode == ALWAYS or signal.deadband.exceeds(self._last.get(key), value):
                    self._last[key] = value
                    self._publisher.offer(group, signal, self._publisher.make_sample(value))

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
