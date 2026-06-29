"""Unit tests for the Modbus type/scaling codec — pure, no device needed."""
import struct

import pytest

from modbus_adapter import codec
from modbus_adapter.codec import COIL, DISCRETE, HOLDING, INPUT, BIG, LITTLE

ORDERS = [(BIG, BIG), (BIG, LITTLE), (LITTLE, BIG), (LITTLE, LITTLE)]

NUMERIC_SAMPLES = {
    "int16": [-32768, -1, 0, 1, 32767],
    "uint16": [0, 1, 255, 65535],
    "int32": [-2147483648, -1, 0, 1, 2147483647],
    "uint32": [0, 1, 4294967295],
    "int64": [-9223372036854775808, -1, 0, 1, 9223372036854775807],
    "uint64": [0, 1, 18446744073709551615],
    "float32": [0.0, 1.5, -2.25, 12.5, 3.5],            # exactly representable in float32
    "float64": [0.0, 1.5, -2.25, 1234.5, 3.141592653589793],
}


@pytest.mark.parametrize("type_", list(NUMERIC_SAMPLES))
@pytest.mark.parametrize("wo,bo", ORDERS)
def test_numeric_roundtrip_all_orders(type_, wo, bo):
    for v in NUMERIC_SAMPLES[type_]:
        regs = codec.encode(HOLDING, v, type_=type_, word_order=wo, byte_order=bo)
        assert len(regs) == codec.register_count(type_)
        assert all(0 <= r <= 0xFFFF for r in regs)
        back = codec.decode(HOLDING, regs, type_=type_, word_order=wo, byte_order=bo)
        if type_ in codec.FLOAT_TYPES:
            assert back == pytest.approx(v)
        else:
            assert back == v


def test_known_layout_uint32():
    # 0x12345678 across the four order combos (encode), and decode back.
    cases = {
        (BIG, BIG): [0x1234, 0x5678],
        (LITTLE, BIG): [0x5678, 0x1234],
        (BIG, LITTLE): [0x3412, 0x7856],
        (LITTLE, LITTLE): [0x7856, 0x3412],
    }
    for (wo, bo), expected in cases.items():
        regs = codec.encode(HOLDING, 0x12345678, type_="uint32", word_order=wo, byte_order=bo)
        assert regs == expected, (wo, bo)
        assert codec.decode(HOLDING, regs, type_="uint32", word_order=wo, byte_order=bo) == 0x12345678


def test_known_float32_50():
    # 50.0f == 0x42480000 -> big/big registers [0x4248, 0x0000]
    assert struct.pack(">f", 50.0) == b"\x42\x48\x00\x00"
    assert codec.decode(HOLDING, [0x4248, 0x0000], type_="float32") == 50.0
    # same value, word order reversed
    assert codec.decode(HOLDING, [0x0000, 0x4248], type_="float32", word_order=LITTLE) == 50.0


def test_int16_signedness():
    assert codec.decode(HOLDING, [0xFFFF], type_="int16") == -1
    assert codec.decode(HOLDING, [0xFFFF], type_="uint16") == 65535
    assert codec.encode(HOLDING, -1, type_="int16") == [0xFFFF]


def test_scale_offset():
    # raw 250 * 0.1 = 25.0 (float result), and inverse on encode
    assert codec.decode(HOLDING, [250], type_="int16", scale=0.1) == pytest.approx(25.0)
    assert codec.encode(HOLDING, 25.0, type_="int16", scale=0.1) == [250]
    # offset too: raw 100 * 2 + 5 = 205
    assert codec.decode(HOLDING, [100], type_="int16", scale=2, offset=5) == pytest.approx(205.0)
    assert codec.encode(HOLDING, 205.0, type_="int16", scale=2, offset=5) == [100]


def test_value_typing_int_vs_float():
    # no scale -> int type stays int; float type is float; scaled int becomes float
    assert isinstance(codec.decode(HOLDING, [100], type_="int16"), int)
    assert isinstance(codec.decode(HOLDING, [0x4248, 0x0000], type_="float32"), float)
    assert isinstance(codec.decode(HOLDING, [100], type_="int16", scale=0.1), float)
    assert isinstance(codec.decode(COIL, [True], type_="bool"), bool)


@pytest.mark.parametrize("wo,bo", ORDERS)
def test_string_roundtrip(wo, bo):
    for text, count in [("Hi", 2), ("Hello", 4), ("", 3), ("ABCDEF", 3)]:
        regs = codec.encode(HOLDING, text, type_="string", count=count, word_order=wo, byte_order=bo)
        assert len(regs) == count
        back = codec.decode(HOLDING, regs, type_="string", count=count, word_order=wo, byte_order=bo)
        assert back == text


def test_bit_extraction():
    reg = 0b0000_0000_0000_1000  # bit 3 set
    assert codec.decode(HOLDING, [reg], type_="bool", bit=3) is True
    assert codec.decode(HOLDING, [reg], type_="bool", bit=0) is False
    assert codec.decode(HOLDING, [reg], type_="bool", bit=2) is False
    assert codec.decode(INPUT, [0xFFFF], type_="bool", bit=15) is True


def test_coil_discrete_passthrough():
    assert codec.decode(COIL, [True], type_="bool") is True
    assert codec.decode(DISCRETE, [0], type_="bool") is False
    assert codec.decode(COIL, [1], type_="bool") is True
    assert codec.encode(COIL, 1, type_="bool") is True
    assert codec.encode(COIL, 0, type_="bool") is False


def test_register_count():
    assert codec.register_count("bool") == 1
    assert codec.register_count("int16") == 1
    assert codec.register_count("uint16") == 1
    assert codec.register_count("int32") == 2
    assert codec.register_count("float32") == 2
    assert codec.register_count("int64") == 4
    assert codec.register_count("float64") == 4
    assert codec.register_count("string", count=5) == 5


def test_errors():
    with pytest.raises(ValueError):
        codec.register_count("string")          # missing count
    with pytest.raises(ValueError):
        codec.register_count("nope")            # unknown type
    with pytest.raises(ValueError):
        codec.encode(HOLDING, "x", type_="string")  # string needs count
