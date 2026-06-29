"""Builds and publishes the Tier-1 ``SouthboundTagUpdate`` envelope (docs/SOUTHBOUND.md §2).

With ``batchMs > 0``, samples are buffered per tag and flushed together by :meth:`flush` (driven by
the device timer); otherwise each sample publishes immediately. Modbus has no device-side timestamp,
so ``sourceTs`` is null and ``serverTs`` is the adapter's read time.
"""
import logging
import threading
from datetime import datetime, timezone

from ggcommons.messaging.message_builder import MessageBuilder

LOGGER = logging.getLogger("modbus_adapter.publisher")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TagUpdatePublisher:
    def __init__(self, messaging, config_manager, config):
        self._messaging = messaging          # MessagingClient (static surface)
        self._cm = config_manager
        self._config = config                # ServerConfiguration
        self._lock = threading.Lock()
        self._pending = {}                   # (unit_id, name) -> [group, tag, [samples]]

    @staticmethod
    def make_sample(value, quality="GOOD", quality_raw="Good", source_ts=None):
        return {"value": value, "quality": quality, "qualityRaw": quality_raw,
                "sourceTs": source_ts, "serverTs": _now_iso()}

    def offer(self, group, tag, sample):
        if self._config.batch_ms > 0:
            key = (group.unit_id, tag.name)
            with self._lock:
                entry = self._pending.get(key)
                if entry is None:
                    self._pending[key] = [group, tag, [sample]]
                else:
                    entry[2].append(sample)
        else:
            self._publish(group, tag, [sample])

    def flush(self):
        with self._lock:
            pending = self._pending
            self._pending = {}
        for group, tag, samples in pending.values():
            if samples:
                self._publish(group, tag, samples)

    def _publish(self, group, tag, samples):
        body = {
            "device": {"adapter": "modbus", "instance": self._config.id,
                       "endpoint": self._config.connection.describe()},
            "tag": {"id": tag.tag_id(group.unit_id), "name": tag.name,
                    "address": tag.address_dict(group.unit_id)},
            "samples": samples,
        }
        msg = MessageBuilder.create("SouthboundTagUpdate", "1.0").with_payload(body).with_config(self._cm).build()
        topic = self._config.resolve_publish_topic(tag.topic, tag.name)
        try:
            self._messaging.publish(topic, msg)
        except Exception as e:  # noqa: BLE001 - a publish failure must not kill the poll loop
            LOGGER.error("[%s] publish to %s failed: %s", self._config.id, topic, e)
