"""Polls each group's tags, coalescing contiguous addresses into the fewest Modbus reads, then
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


def coalesce(tags, max_gap):
    """Group same-table tags into read blocks, merging spans separated by <= ``max_gap`` and capping
    each block at the protocol max. Returns blocks ``{table, start, end, length, tags[]}``."""
    blocks = []
    by_table = defaultdict(list)
    for t in tags:
        by_table[t.table].append(t)
    for table, ts in by_table.items():
        cap = MAX_READ[table]
        cur = None
        for t in sorted(ts, key=lambda x: x.address):
            end = t.address + t.unit_length()
            if cur is not None and t.address <= cur["end"] + max_gap and (end - cur["start"]) <= cap:
                cur["end"] = max(cur["end"], end)
                cur["tags"].append(t)
            else:
                if cur is not None:
                    blocks.append(cur)
                cur = {"table": table, "start": t.address, "end": end, "tags": [t]}
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
            blocks = coalesce(group.tags, group.max_gap)
            self._blocks[group.id] = blocks
            n_tags = sum(len(b["tags"]) for b in blocks)
            LOGGER.info("[%s] poll group '%s': %d tag(s) in %d read block(s) @ %dms",
                        self._config.id, group.id, n_tags, len(blocks), group.poll_interval_ms)
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
            except Exception as e:  # noqa: BLE001 - block read failed -> BAD for its tags
                raw = str(e) or "read error"
                for tag in block["tags"]:
                    self._counters.increment_read_error()
                    self._publisher.offer(group, tag, self._publisher.make_sample(None, "BAD", raw))
                continue
            for tag in block["tags"]:
                off = tag.address - block["start"]
                slice_ = data[off: off + tag.unit_length()]
                try:
                    value = codec.decode(tag.table, slice_, type_=tag.type, word_order=tag.word_order,
                                         byte_order=tag.byte_order, scale=tag.scale, offset=tag.offset,
                                         count=tag.count, bit=tag.bit)
                except Exception as e:  # noqa: BLE001
                    self._counters.increment_read_error()
                    self._publisher.offer(group, tag, self._publisher.make_sample(None, "BAD", str(e)))
                    continue
                self._counters.increment_read()
                key = (group.unit_id, tag.name)
                if group.publish_mode == ALWAYS or tag.deadband.exceeds(self._last.get(key), value):
                    self._last[key] = value
                    self._publisher.offer(group, tag, self._publisher.make_sample(value))

    def resolved_tags(self):
        out = []
        for group in self._config.poll_groups:
            for tag in group.tags:
                out.append({"name": tag.name, "unitId": group.unit_id,
                            "tagId": tag.tag_id(group.unit_id), "address": tag.address_dict(group.unit_id)})
        return out

    def stop(self):
        self._stop.set()
        for th in self._threads:
            th.join(timeout=2.0)
