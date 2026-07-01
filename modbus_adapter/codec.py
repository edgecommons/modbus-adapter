"""Modbus type & scaling codec.

Modbus carries only bits (coils / discrete inputs) and 16-bit registers (holding / input). This
module synthesizes the richer types the southbound contract speaks — ``bool``, signed/unsigned
integers (16/32/64), IEEE floats (32/64), and strings — out of those primitives, with configurable
byte order, word order, an optional linear ``scale``/``offset`` transform, and single-bit extraction.

Pure and stateless (operates on plain ``int``/``bool`` lists), so it is fully unit-testable without a
device. The poll/command layers call :func:`decode` on read results and :func:`encode` on writes.

Order semantics for a multi-register value:
  * **word order** ``big`` (default) = most-significant register first; ``little`` = registers reversed.
  * **byte order** ``big`` (default) = high byte first within each register; ``little`` = bytes swapped.
Together they cover the four real-world 32/64-bit layouts (ABCD / DCBA / BADC / CDAB).
"""
import struct

# --- Modbus tables ---------------------------------------------------------------------------
COIL = "coil"            # FC1, read/write, 1 bit
DISCRETE = "discrete"    # FC2, read-only, 1 bit
HOLDING = "holding"      # FC3, read/write, 16-bit register
INPUT = "input"          # FC4, read-only, 16-bit register

TABLES = {COIL, DISCRETE, HOLDING, INPUT}
BIT_TABLES = {COIL, DISCRETE}
REGISTER_TABLES = {HOLDING, INPUT}
WRITABLE_TABLES = {COIL, HOLDING}

# --- types: name -> (big-endian struct format, register count) -------------------------------
_FMT = {
    "int16": (">h", 1), "uint16": (">H", 1),
    "int32": (">i", 2), "uint32": (">I", 2),
    "int64": (">q", 4), "uint64": (">Q", 4),
    "float32": (">f", 2), "float64": (">d", 4),
}
INT_TYPES = {"int16", "uint16", "int32", "uint32", "int64", "uint64"}
FLOAT_TYPES = {"float32", "float64"}
NUMERIC_TYPES = INT_TYPES | FLOAT_TYPES
TYPES = NUMERIC_TYPES | {"bool", "string"}

BIG = "big"
LITTLE = "little"


def register_count(type_: str, count=None) -> int:
    """Registers spanned by a holding/input signal of the given type (``count`` for ``string``)."""
    if type_ == "bool":
        return 1                      # a bool on a register table is one register (a bit of it)
    if type_ == "string":
        if not count:
            raise ValueError("a 'string' signal requires 'count' (number of registers)")
        return int(count)
    try:
        return _FMT[type_][1]
    except KeyError:
        raise ValueError(f"unknown Modbus type '{type_}'") from None


# --- decode (read result -> JSON-native value) -----------------------------------------------
def decode(table, raw, *, type_, word_order=BIG, byte_order=BIG,
           scale=None, offset=None, count=None, bit=None):
    """Decode a read result into a JSON-native value.

    ``raw`` is a list of bools for coil/discrete tables, or a list of register ints for
    holding/input tables.
    """
    if table in BIT_TABLES:
        return bool(raw[0])
    # holding / input
    if type_ == "bool":
        b = 0 if bit is None else int(bit)
        return bool((raw[0] >> b) & 1)
    if type_ == "string":
        return _decode_string(raw, word_order, byte_order)
    fmt, n = _fmt(type_)
    value = struct.unpack(fmt, _assemble(raw[:n], word_order, byte_order))[0]
    return _apply_scale(value, scale, offset)


# --- encode (JSON value -> coil bool / register list) ----------------------------------------
def encode(table, value, *, type_, word_order=BIG, byte_order=BIG,
           scale=None, offset=None, count=None):
    """Encode a value for writing: a ``bool`` for coil tables, else a list of register ints.

    (Single-bit writes to a holding register are a read-modify-write handled by the caller, not
    here.)
    """
    if table in BIT_TABLES:
        return bool(value)
    if type_ == "string":
        return _encode_string(value, count, word_order, byte_order)
    fmt, _ = _fmt(type_)
    raw = _unapply_scale(value, type_, scale, offset)
    return _disassemble(struct.pack(fmt, raw), word_order, byte_order)


# --- internals -------------------------------------------------------------------------------
def _fmt(type_):
    try:
        return _FMT[type_]
    except KeyError:
        raise ValueError(f"unknown Modbus numeric type '{type_}'") from None


def _assemble(regs, word_order, byte_order) -> bytes:
    """Registers -> big-endian byte string, honoring word/byte order."""
    words = list(reversed(regs)) if word_order == LITTLE else list(regs)
    out = bytearray()
    for w in words:
        b = struct.pack(">H", w & 0xFFFF)
        if byte_order == LITTLE:
            b = b[::-1]
        out += b
    return bytes(out)


def _disassemble(data, word_order, byte_order):
    """Big-endian byte string -> register list, honoring word/byte order."""
    words = []
    for i in range(0, len(data), 2):
        b = data[i:i + 2]
        if byte_order == LITTLE:
            b = b[::-1]
        words.append(struct.unpack(">H", b)[0])
    if word_order == LITTLE:
        words.reverse()
    return words


def _apply_scale(value, scale, offset):
    s = 1.0 if scale is None else float(scale)
    o = 0.0 if offset is None else float(offset)
    if s == 1.0 and o == 0.0:
        return value                  # preserve int-ness (int types) / float (float types)
    return value * s + o


def _unapply_scale(value, type_, scale, offset):
    s = 1.0 if scale is None else float(scale)
    o = 0.0 if offset is None else float(offset)
    raw = value if (s == 1.0 and o == 0.0) else (value - o) / s
    if type_ in INT_TYPES:
        return int(round(raw))
    return float(raw)


def _decode_string(regs, word_order, byte_order) -> str:
    data = _assemble(regs, word_order, byte_order)
    return data.decode("utf-8", errors="replace").rstrip("\x00")


def _encode_string(value, count, word_order, byte_order):
    if not count:
        raise ValueError("a 'string' signal requires 'count' (number of registers)")
    data = str(value).encode("utf-8")[: count * 2].ljust(count * 2, b"\x00")
    return _disassemble(data, word_order, byte_order)
