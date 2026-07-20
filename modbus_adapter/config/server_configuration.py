"""Per-instance configuration resolver — the Modbus analog of the OPC UA ServerConfiguration.

Resolves an instance's connection, its timing defaults (instance ▸ global ▸ built-in), its write
allow-list, and its poll groups. Topic construction is no longer config-driven: data updates and the
command surface address the Unified Namespace via ``gg.uns()`` / the command inbox, so the legacy
publish / write / read / control topic templates are gone.

Writes are gated by a per-entry **allow-list** (``writes.allow[]``, SOUTHBOUND.md §2.2 / D-U16): a
signal is writable only when its stable ``signal.id`` is on the list, checked before any device I/O.
An empty list — the default — means the instance is read-only, the correct posture for anything
touching a control system.
"""

#: ``component.global.healthThresholds.staleSignalSecs`` default (SOUTHBOUND.md §5).
DEFAULT_STALE_SIGNAL_SECS = 30
from .connection_info import ConnectionInfo
from .poll_group import ON_CHANGE, PollGroup, normalize_publish_mode


class ServerConfiguration:
    def __init__(self, config_manager, global_config, instance_id):
        self._cm = config_manager
        inst = config_manager.get_instance_config(instance_id) or {}
        glob = global_config or {}
        self.id = inst.get("id", instance_id)
        self.connection = ConnectionInfo(inst.get("connection"))

        inst_def = inst.get("defaults", {})
        glob_def = glob.get("defaults", {})

        def _default(key, fallback):
            if key in inst_def:
                return inst_def[key]
            if key in glob_def:
                return glob_def[key]
            return fallback

        self.poll_interval_ms = int(_default("pollIntervalMs", 1000))
        self.publish_mode = normalize_publish_mode(_default("publishMode", ON_CHANGE))
        self.max_gap = int(_default("maxGap", 0))

        pub = inst.get("publish", {})
        self.batch_ms = int(pub.get("batchMs", _default("batchMs", 0)))

        # The write allow-list (SOUTHBOUND.md §2.2 / D-U16): stable signal.ids this instance may
        # write. Empty (the default) => read-only.
        writes = inst.get("writes") or {}
        allow = writes.get("allow") or []
        if not isinstance(allow, list):
            raise ValueError(f"instance '{self.id}': writes.allow must be an array of signal ids")
        self.writes_allow = [str(a) for a in allow]

        # Staleness threshold for southbound_health.staleSignals (SOUTHBOUND.md §5).
        thresholds = glob.get("healthThresholds") or {}
        self.stale_signal_secs = int(thresholds.get("staleSignalSecs", DEFAULT_STALE_SIGNAL_SECS))

        self.poll_groups = [
            PollGroup.from_dict(g, self, i) for i, g in enumerate(inst.get("pollGroups", []))
        ]

    def permits(self, signal_id) -> bool:
        """Whether ``signal_id`` is on this instance's ``writes.allow`` list. Nothing else is
        writable, whatever an ``sb/write`` command asks for — matched on the stable ``signal.id``
        (never a volatile index), before any device I/O."""
        return signal_id in self.writes_allow

    def all_signals(self):
        """(poll_group, signal) for every configured signal — used by the command/control surfaces."""
        return [(g, s) for g in self.poll_groups for s in g.signals]
