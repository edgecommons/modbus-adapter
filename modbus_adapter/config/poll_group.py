"""A poll group: a set of signals read together on one interval against one unit id."""

from .signal_spec import SignalSpec

ON_CHANGE = "onChange"
ALWAYS = "always"


def normalize_publish_mode(value):
    """Return the bounded publish-mode vocabulary used for behavior and metric dimensions."""
    return ALWAYS if value == ALWAYS else ON_CHANGE


class PollGroup:
    """Signals polled together. ``maxGap`` lets the coalescer merge signals separated by small address
    gaps into one Modbus read; ``publishMode`` is ``onChange`` (deadband-gated) or ``always``."""

    def __init__(self, id_, poll_interval_ms, unit_id, publish_mode, max_gap, signals):
        self.id = id_
        self.poll_interval_ms = poll_interval_ms
        self.unit_id = unit_id
        self.publish_mode = publish_mode
        self.max_gap = max_gap
        self.signals = signals

    @staticmethod
    def from_dict(o, server_config, index=0):
        return PollGroup(
            id_=o.get("id") or f"group-{index + 1}",
            poll_interval_ms=int(o.get("pollIntervalMs", server_config.poll_interval_ms)),
            unit_id=int(o.get("unitId", server_config.connection.unit_id)),
            publish_mode=normalize_publish_mode(o.get("publishMode", server_config.publish_mode)),
            max_gap=int(o.get("maxGap", server_config.max_gap)),
            signals=[SignalSpec.from_dict(s) for s in o.get("signals", [])],
        )
