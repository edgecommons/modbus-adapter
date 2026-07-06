"""Batches/publishes Modbus reads through the instance's ``data()`` facade
(``docs/platform/DESIGN-class-facades.md`` §2.1, ``edgecommons.facades.data_facade.DataFacade``) --
the facade constructs the ``SouthboundSignalUpdate`` body (``device``/``signal``/``samples``), mints
the UNS ``data`` topic, stamps the envelope identity, and applies the quality/timestamp defaults, so
this module never hand-assembles the body or the topic for a normal read.

Modbus has **no native quality notion**: a successful read passes no ``quality`` at all, so the
facade defaults it to ``GOOD`` with ``qualityRaw: "unspecified"`` -- the DESIGN-class-facades §2.1
"source with no native quality codes" case. A failed read passes an explicit
:attr:`~edgecommons.facades.quality.Quality.BAD` with the raw error text as ``qualityRaw`` -- **and no
value at all** (a whole read block failed, so there is nothing to report). The ``data()`` facade's
``samples[]`` structurally REQUIRES a value (DESIGN-class-facades §5.2, D2 -- the only hard reject
besides ``signal.id``), so a value-less sample cannot pass through the normal builder; it uses the
facade's raw escape hatch (``publish_body``, D5) instead (see :meth:`SignalUpdatePublisher._publish`).
Java's OPC UA adapter doesn't hit this: it encodes a missing OPC UA value as Gson's ``JsonNull`` --
a sentinel object distinct from "no value" -- but Python's plain ``None`` can't make that
distinction, so the escape hatch is the correct Python mirror of the same "no value" case, not a
regression to hand-rolled publishing (the topic is still minted, and the envelope identity still
stamped, by this same instance's ``data()`` facade).

With ``batchMs > 0``, samples are buffered per signal and flushed together by :meth:`flush` (driven by
the device timer); otherwise each sample publishes immediately. Modbus has no device-side timestamp,
so ``sourceTs`` stays ``None`` and ``serverTs`` is filled by the facade (the adapter's read time).
"""
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from edgecommons.facades.data_facade import DataFacade
from edgecommons.facades.quality import Quality
from edgecommons.facades.signal_update import Sample
from edgecommons.facades.util import format_instant

LOGGER = logging.getLogger("modbus_adapter.publisher")


class SignalUpdatePublisher:
    def __init__(self, data_facade, config):
        self._data = data_facade              # this instance's DataFacade (gg.instance(id).data())
        self._config = config                 # ServerConfiguration
        self._lock = threading.Lock()
        self._pending = {}                    # (unit_id, name) -> [group, signal, [Sample, ...]]

    @staticmethod
    def make_sample(value, quality: Optional[Quality] = None, quality_raw: Optional[str] = None,
                    source_ts: Optional[str] = None) -> Sample:
        """Builds one :class:`~edgecommons.facades.signal_update.Sample` for a read.
        ``quality=None`` (the normal successful-read case) leaves it for the ``data()`` facade to
        default to ``GOOD``/``qualityRaw:"unspecified"`` -- Modbus has no native quality codes. A
        failed read passes ``value=None`` with an explicit :attr:`Quality.BAD` (+ the raw error
        text as ``quality_raw``) -- see the module docstring for how a value-less sample is
        published."""
        return Sample(value, quality, quality_raw, source_ts, None)

    def offer(self, group, signal, sample: Sample) -> None:
        if self._config.batch_ms > 0:
            key = (group.unit_id, signal.name)
            with self._lock:
                entry = self._pending.get(key)
                if entry is None:
                    self._pending[key] = [group, signal, [sample]]
                else:
                    entry[2].append(sample)
        else:
            self._publish(group, signal, [sample])

    def flush(self) -> None:
        with self._lock:
            pending = self._pending
            self._pending = {}
        for group, signal, samples in pending.values():
            if samples:
                self._publish(group, signal, samples)

    def _publish(self, group, signal, samples: List[Sample]) -> None:
        valued = [s for s in samples if s.value is not None]
        valueless = [s for s in samples if s.value is None]
        try:
            if valued:
                self._data.signal(signal.signal_id(group.unit_id)) \
                    .name(signal.name) \
                    .address(signal.address_dict(group.unit_id)) \
                    .device(adapter="modbus", instance=self._config.id,
                            endpoint=self._config.connection.describe()) \
                    .add_samples(valued) \
                    .signal_path(signal.name) \
                    .publish()
            if valueless:
                self._publish_valueless(group, signal, valueless)
        except Exception as e:  # noqa: BLE001 - a publish failure must not kill the poll loop
            LOGGER.error("[%s] data publish for '%s' failed: %s", self._config.id, signal.name, e)

    def _publish_valueless(self, group, signal, samples: List[Sample]) -> None:
        """A fully failed block read carries **no value at all** for its signals -- the module
        docstring explains why that can't go through the normal builder. Uses
        :meth:`~edgecommons.facades.data_facade.DataFacade.publish_body` (the raw escape hatch) to
        publish the historical ``{"value": None, "quality": "BAD", ...}`` shape verbatim; the
        topic is still minted and the identity still stamped by this same instance's ``data()``
        facade -- only the per-sample defaulting is done by hand here (mirroring
        :meth:`DataFacade.build_body`'s own rules, in case a future caller omits quality)."""
        body = {
            "device": {"adapter": "modbus", "instance": self._config.id,
                       "endpoint": self._config.connection.describe()},
            "signal": {"id": signal.signal_id(group.unit_id), "name": signal.name,
                       "address": signal.address_dict(group.unit_id)},
            "samples": [self._valueless_sample_dict(s) for s in samples],
        }
        self._data.publish_body(signal.name, body)

    @staticmethod
    def _valueless_sample_dict(sample: Sample) -> Dict[str, Any]:
        quality_defaulted = sample.quality is None
        quality = (Quality.GOOD if quality_defaulted else sample.quality).wire()
        quality_raw = sample.quality_raw
        if quality_raw is None and quality_defaulted:
            quality_raw = DataFacade.QUALITY_UNSPECIFIED
        return {
            "value": sample.value,
            "quality": quality,
            "qualityRaw": quality_raw,
            "sourceTs": sample.source_ts,
            "serverTs": sample.server_ts if sample.server_ts is not None
            else format_instant(datetime.now(timezone.utc)),
        }
