"""Unit tests for the poll-manager register coalescing (pure, no device)."""
from modbus_adapter.config.tag_spec import TagSpec
from modbus_adapter.poll_manager import coalesce


def _tag(name, table, address, type_="uint16", **kw):
    return TagSpec.from_dict({"name": name, "table": table, "address": address, "type": type_, **kw})


def _block_set(blocks):
    return sorted((b["table"], b["start"], b["length"], len(b["tags"])) for b in blocks)


def test_contiguous_merge():
    tags = [_tag("a", "holding", 0), _tag("b", "holding", 1)]
    blocks = coalesce(tags, max_gap=0)
    assert _block_set(blocks) == [("holding", 0, 2, 2)]


def test_gap_within_maxgap_merges():
    tags = [_tag("a", "holding", 0), _tag("b", "holding", 5)]
    assert _block_set(coalesce(tags, max_gap=4)) == [("holding", 0, 6, 2)]
    # gap of 4 (1 -> 5) exceeds max_gap 3 -> split (each block is length 1)
    assert _block_set(coalesce(tags, max_gap=3)) == [("holding", 0, 1, 1), ("holding", 5, 1, 1)]


def test_multiregister_spans_merge():
    tags = [_tag("f", "holding", 0, "float32"), _tag("w", "holding", 2, "uint16")]
    assert _block_set(coalesce(tags, max_gap=0)) == [("holding", 0, 3, 2)]


def test_different_tables_separate():
    tags = [_tag("c", "coil", 0, "bool"), _tag("h", "holding", 0)]
    assert _block_set(coalesce(tags, max_gap=100)) == [("coil", 0, 1, 1), ("holding", 0, 1, 1)]


def test_read_size_cap_splits():
    # two registers 125 apart -> span 126 > 125 cap -> two blocks even with a huge gap allowance
    tags = [_tag("a", "holding", 0), _tag("b", "holding", 125)]
    assert _block_set(coalesce(tags, max_gap=1000)) == [("holding", 0, 1, 1), ("holding", 125, 1, 1)]


def test_unsorted_input_is_ordered():
    tags = [_tag("b", "holding", 2), _tag("a", "holding", 0), _tag("c", "holding", 1)]
    blocks = coalesce(tags, max_gap=0)
    assert _block_set(blocks) == [("holding", 0, 3, 3)]
    # tags within the block are in address order for correct slicing
    assert [t.address for t in blocks[0]["tags"]] == [0, 1, 2]
