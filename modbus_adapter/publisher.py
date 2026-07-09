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
besides ``signal.id``), so a value-less sample cannot pass through the normal builder. It uses the
facade's pre-built-body path (``publish_body``, D5) instead (see
:meth:`SignalUpdatePublisher._publish`), which still mints the topic, stamps the envelope identity
and tags, and serializes as the typed ``SouthboundSignalUpdate`` protobuf body.

With ``batchMs > 0``, samples are buffered per signal and flushed together by :meth:`flush` (driven by
the device timer); otherwise each sample publishes immediately. Modbus has no device-origin timestamp,
so ``sourceTs`` is not synthesized. Successful Modbus polls stamp ``serverTs`` with the register-read
completion time; if a caller omits it, the facade still defaults ``serverTs`` to the publish time.
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
    def __init__(self, data_facade, config, operational_metrics=None):
        self._data = data_facade              # this instance's DataFacade (gg.instance(id).data())
        self._config = config                 # ServerConfiguration
        self._operational_metrics = operational_metrics
        self._lock = threading.Lock()
        self._pending = {}                    # (unit_id, name) -> [group, signal, [Sample, ...]]

    @staticmethod
    def make_sample(value, quality: Optional[Quality] = None, quality_raw: Optional[str] = None,
                    source_ts: Optional[str] = None, server_ts: Optional[str] = None) -> Sample:
        """Builds one :class:`~edgecommons.facades.signal_update.Sample` for a read.
        ``quality=None`` (the normal successful-read case) leaves it for the ``data()`` facade to
        default to ``GOOD``/``qualityRaw:"unspecified"`` -- Modbus has no native quality codes. A
        failed read passes ``value=None`` with an explicit :attr:`Quality.BAD` (+ the raw error
        text as ``quality_raw``) -- see the module docstring for how a value-less sample is
        published."""
        return Sample(value, quality, quality_raw, source_ts, server_ts)

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
                self._record_publish(group.publish_mode, batchFlushes=1)
                self._publish(group, signal, samples)

    def _publish(self, group, signal, samples: List[Sample]) -> None:
        t0 = datetime.now(timezone.utc)
        valued = [s for s in samples if s.value is not None]
        valueless = [s for s in samples if s.value is None]
        published_messages = 0
        published_samples = 0
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
                published_messages += 1
                published_samples += len(valued)
            if valueless:
                self._publish_valueless(group, signal, valueless)
                published_messages += 1
                published_samples += len(valueless)
        except Exception as e:  # noqa: BLE001 - a publish failure must not kill the poll loop
            LOGGER.error("[%s] data publish for '%s' failed: %s", self._config.id, signal.name, e)
            self._record_publish(group.publish_mode, publishFailures=1)
        finally:
            elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000.0
            if published_messages:
                self._record_publish(
                    group.publish_mode,
                    dataMessagesPublished=published_messages,
                    samplesPublished=published_samples,
                    batchSize=len(samples),
                    publishLatencyMs=elapsed_ms,
                )

    def _publish_valueless(self, group, signal, samples: List[Sample]) -> None:
        """A fully failed block read carries **no value at all** for its signals -- the module
        docstring explains why that can't go through the normal builder. Uses
        :meth:`~edgecommons.facades.data_facade.DataFacade.publish_body` to publish the same
        ``SouthboundSignalUpdate`` body shape with a protobuf null sample value; the topic,
        identity, and tags are still minted by this same instance's ``data()`` facade."""
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
        out = {
            "value": sample.value,
            "quality": quality,
            "qualityRaw": quality_raw,
            "serverTs": sample.server_ts if sample.server_ts is not None
            else format_instant(datetime.now(timezone.utc)),
        }
        if sample.source_ts is not None:
            out["sourceTs"] = sample.source_ts
        return out

    def _record_publish(self, publish_mode, **values):
        if self._operational_metrics is not None:
            self._operational_metrics.record_publish(publish_mode, **values)
