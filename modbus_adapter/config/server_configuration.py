"""Per-instance configuration resolver — the Modbus analog of the OPC UA ServerConfiguration.

Resolves an instance's connection, timing defaults (instance ▸ global ▸ built-in), the publish /
write / read / control topic templates, and its poll groups.
"""
import re

from .connection_info import ConnectionInfo
from .poll_group import PollGroup

_DEFAULT_PUBLISH = "southbound/{ComponentName}/{InstanceId}/{signalId}"
_DEFAULT_WRITE = "southbound/{ComponentName}/{InstanceId}/write"
_DEFAULT_READ = "southbound/{ComponentName}/{InstanceId}/read"
_CONTROL = "southbound/{ComponentName}/{InstanceId}/control/+"


def _sanitize(value: str) -> str:
    """Strip path separators / MQTT wildcards / whitespace from a topic segment (injection guard)."""
    return re.sub(r"[/+#\s]", "_", str(value))


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
        self.publish_topic_template = pub.get("topic", _DEFAULT_PUBLISH)
        self.batch_ms = int(pub.get("batchMs", _default("batchMs", 0)))

        write = inst.get("write", {})
        self.write_enabled = write.get("enabled", False) is True
        self.write_topic = self.resolve_template(write.get("topic", _DEFAULT_WRITE))

        read = inst.get("read", {})
        self.read_topic = self.resolve_template(read.get("topic", _DEFAULT_READ))
        self.control_topic = self.resolve_template(_CONTROL)

        self.poll_groups = [PollGroup.from_dict(g, self) for g in inst.get("pollGroups", [])]

    def resolve_template(self, template: str) -> str:
        """Resolve {ThingName}/{ComponentName}/{ComponentFullName}/custom tags via the lib, then
        substitute the adapter-specific {InstanceId}."""
        return self._cm.resolve_template(template).replace("{InstanceId}", _sanitize(self.id))

    def resolve_publish_topic(self, override_template, signal_name: str) -> str:
        template = override_template or self.publish_topic_template
        return self.resolve_template(template).replace("{signalId}", _sanitize(signal_name))

    def all_signals(self):
        """(poll_group, signal) for every configured signal — used by the command/control surfaces."""
        return [(g, s) for g in self.poll_groups for s in g.signals]
