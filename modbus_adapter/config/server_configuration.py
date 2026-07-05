"""Per-instance configuration resolver — the Modbus analog of the OPC UA ServerConfiguration.

Resolves an instance's connection, its timing defaults (instance ▸ global ▸ built-in), whether writes
are enabled, and its poll groups. Topic construction is no longer config-driven: data updates and the
command surface address the Unified Namespace via ``gg.uns()`` / the command inbox, so the legacy
publish / write / read / control topic templates are gone.
"""
from .connection_info import ConnectionInfo
from .poll_group import PollGroup


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
        self.publish_mode = _default("publishMode", "onChange")
        self.max_gap = int(_default("maxGap", 0))

        pub = inst.get("publish", {})
        self.batch_ms = int(pub.get("batchMs", _default("batchMs", 0)))

        write = inst.get("write", {})
        self.write_enabled = write.get("enabled", False) is True

        self.poll_groups = [PollGroup.from_dict(g, self) for g in inst.get("pollGroups", [])]

    def all_signals(self):
        """(poll_group, signal) for every configured signal — used by the command/control surfaces."""
        return [(g, s) for g in self.poll_groups for s in g.signals]
