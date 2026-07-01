"""Unit tests for the poll-manager register coalescing (pure, no device)."""
from modbus_adapter.config.signal_spec import SignalSpec
from modbus_adapter.poll_manager import coalesce


def _signal(name, table, address, type_="uint16", **kw):
    return SignalSpec.from_dict({"name": name, "table": table, "address": address, "type": type_, **kw})


def _block_set(blocks):
    return sorted((b["table"], b["start"], b["length"], len(b["signals"])) for b in blocks)


def test_contiguous_merge():
    signals = [_signal("a", "holding", 0), _signal("b", "holding", 1)]
    blocks = coalesce(signals, max_gap=0)
    assert _block_set(blocks) == [("holding", 0, 2, 2)]


def test_gap_within_maxgap_merges():
    signals = [_signal("a", "holding", 0), _signal("b", "holding", 5)]
    assert _block_set(coalesce(signals, max_gap=4)) == [("holding", 0, 6, 2)]
    # gap of 4 (1 -> 5) exceeds max_gap 3 -> split (each block is length 1)
    assert _block_set(coalesce(signals, max_gap=3)) == [("holding", 0, 1, 1), ("holding", 5, 1, 1)]


def test_multiregister_spans_merge():
    signals = [_signal("f", "holding", 0, "float32"), _signal("w", "holding", 2, "uint16")]
    assert _block_set(coalesce(signals, max_gap=0)) == [("holding", 0, 3, 2)]


def test_different_tables_separate():
    signals = [_signal("c", "coil", 0, "bool"), _signal("h", "holding", 0)]
    assert _block_set(coalesce(signals, max_gap=100)) == [("coil", 0, 1, 1), ("holding", 0, 1, 1)]


def test_read_size_cap_splits():
    # two registers 125 apart -> span 126 > 125 cap -> two blocks even with a huge gap allowance
    signals = [_signal("a", "holding", 0), _signal("b", "holding", 125)]
    assert _block_set(coalesce(signals, max_gap=1000)) == [("holding", 0, 1, 1), ("holding", 125, 1, 1)]


def test_unsorted_input_is_ordered():
    signals = [_signal("b", "holding", 2), _signal("a", "holding", 0), _signal("c", "holding", 1)]
    blocks = coalesce(signals, max_gap=0)
    assert _block_set(blocks) == [("holding", 0, 3, 3)]
    # signals within the block are in address order for correct slicing
    assert [s.address for s in blocks[0]["signals"]] == [0, 1, 2]
