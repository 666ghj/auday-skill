"""Browse manual recording marks while preserving the phone's record format."""

WIRE_KINDS = ("transcript", "highlight", "summary", "reflection", "memory")
SEARCH_KINDS = (*WIRE_KINDS, "mark")
MARK_PREFIX = "mark:"


def display_kind(record):
    # The phone reserves mark: for individual marks. AI highlights can mention
    # manualMarkIDs or marking in their text without being a mark themselves.
    kind = record.get("kind", "unknown")
    if kind == "highlight" and record["id"].startswith(MARK_PREFIX):
        return "mark"
    return kind


def search_result(record):
    if display_kind(record) == "mark":
        return {**record, "displayKind": "mark"}
    return record


def kind_filter(kind):
    """SQL over raw records (aliased r) in both the source and semantic DB."""
    if kind == "mark":
        return "r.kind = ? AND r.id GLOB ?", ("highlight", MARK_PREFIX + "*")
    if kind == "highlight":
        return "r.kind = ? AND r.id NOT GLOB ?", ("highlight", MARK_PREFIX + "*")
    return "r.kind = ?", (kind,)
