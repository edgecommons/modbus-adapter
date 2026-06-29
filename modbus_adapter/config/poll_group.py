"""A poll group: a set of tags read together on one interval against one unit id."""
import uuid

from .tag_spec import TagSpec

ON_CHANGE = "onChange"
ALWAYS = "always"


class PollGroup:
    """Tags polled together. ``maxGap`` lets the coalescer merge tags separated by small address
    gaps into one Modbus read; ``publishMode`` is ``onChange`` (deadband-gated) or ``always``."""

    def __init__(self, id_, poll_interval_ms, unit_id, publish_mode, max_gap, tags):
        self.id = id_
        self.poll_interval_ms = poll_interval_ms
        self.unit_id = unit_id
        self.publish_mode = publish_mode
        self.max_gap = max_gap
        self.tags = tags

    @staticmethod
    def from_dict(o, server_config):
        return PollGroup(
            id_=o.get("id") or str(uuid.uuid4()),
            poll_interval_ms=int(o.get("pollIntervalMs", server_config.poll_interval_ms)),
            unit_id=int(o.get("unitId", server_config.connection.unit_id)),
            publish_mode=o.get("publishMode", server_config.publish_mode),
            max_gap=int(o.get("maxGap", server_config.max_gap)),
            tags=[TagSpec.from_dict(t) for t in o.get("tags", [])],
        )
