"""One Modbus tag: a named, typed view onto a register/bit address."""
from .. import codec
from .deadband_spec import DeadbandSpec


class TagSpec:
    """A single tag. ``table``+``address`` locate it; ``type`` (+ order/scale/bit) decode its value.

    Unlike OPC UA (which discovers nodes), Modbus tags are declared explicitly — there is no browse.
    """

    def __init__(self, name, table, address, type_="uint16", count=None,
                 word_order=codec.BIG, byte_order=codec.BIG, bit=None,
                 scale=None, offset=None, deadband=None, topic=None):
        self.name = name
        self.table = table
        self.address = address
        self.type = type_
        self.count = count
        self.word_order = word_order
        self.byte_order = byte_order
        self.bit = bit
        self.scale = scale
        self.offset = offset
        self.deadband = deadband or DeadbandSpec()
        self.topic = topic

    @staticmethod
    def from_dict(o):
        name = o.get("name")
        if not name:
            raise ValueError(f"tag missing 'name': {o}")
        table = o.get("table")
        if table not in codec.TABLES:
            raise ValueError(f"tag '{name}': table must be one of {sorted(codec.TABLES)}, got {table!r}")
        if "address" not in o:
            raise ValueError(f"tag '{name}': missing 'address'")
        address = int(o["address"])
        if address < 0:
            raise ValueError(f"tag '{name}': address must be >= 0")
        # bit tables are always bool; register tables default to uint16
        if table in codec.BIT_TABLES:
            type_ = o.get("type", "bool")
            if type_ != "bool":
                raise ValueError(f"tag '{name}': {table} tags must be type 'bool'")
        else:
            type_ = o.get("type", "uint16")
            if type_ not in codec.TYPES:
                raise ValueError(f"tag '{name}': unknown type {type_!r}")
        count = o.get("count")
        if type_ == "string" and not count:
            raise ValueError(f"tag '{name}': 'string' type requires 'count'")
        bit = o.get("bit")
        if bit is not None and (table in codec.BIT_TABLES or type_ != "bool"):
            raise ValueError(f"tag '{name}': 'bit' is only valid for a bool on a holding/input register")
        return TagSpec(
            name=name, table=table, address=address, type_=type_, count=count,
            word_order=o.get("wordOrder", codec.BIG), byte_order=o.get("byteOrder", codec.BIG),
            bit=bit, scale=o.get("scale"), offset=o.get("offset"),
            deadband=DeadbandSpec.from_dict(o.get("deadband")), topic=o.get("topic"),
        )

    def unit_length(self) -> int:
        """Read length in protocol units: bits for coil/discrete, registers for holding/input."""
        if self.table in codec.BIT_TABLES:
            return 1
        return codec.register_count(self.type, self.count)

    def address_dict(self, unit_id) -> dict:
        """The protocol-native ``tag.address`` published in the envelope."""
        a = {"unitId": unit_id, "table": self.table, "address": self.address, "type": self.type}
        if self.type in codec.NUMERIC_TYPES or self.type == "string":
            a["wordOrder"] = self.word_order
            a["byteOrder"] = self.byte_order
        if self.count is not None:
            a["count"] = self.count
        if self.bit is not None:
            a["bit"] = self.bit
        return a

    def tag_id(self, unit_id) -> str:
        """Stable canonical id, e.g. ``u1/holding/40/float32`` (the OPC UA ``tag.id`` analog)."""
        return f"u{unit_id}/{self.table}/{self.address}/{self.type}"
