"""Client-side change/deadband policy for a polled tag."""

NONE = "none"
ABSOLUTE = "absolute"
PERCENT = "percent"


class DeadbandSpec:
    """Decides whether a new value differs enough from the last published value to republish.

    ``none`` republishes on any change; ``absolute`` requires ``|new-old| >= value``; ``percent``
    requires the change to be at least ``value`` percent of the old value.
    """

    def __init__(self, type_=NONE, value=0.0):
        self.type = type_
        self.value = float(value)

    @staticmethod
    def from_dict(o):
        if not o:
            return DeadbandSpec()
        return DeadbandSpec(o.get("type", NONE), o.get("value", 0.0))

    def exceeds(self, old, new) -> bool:
        """True if ``new`` should be published given the last published ``old`` (None = first)."""
        if old is None:
            return True
        if self.type == NONE:
            return new != old
        try:
            delta = abs(float(new) - float(old))
        except (TypeError, ValueError):
            return new != old           # non-numeric (bool/string) -> any change
        if self.type == ABSOLUTE:
            return delta >= self.value
        if self.type == PERCENT:
            base = abs(float(old))
            if base == 0.0:
                return new != old
            return (delta / base * 100.0) >= self.value
        return new != old
