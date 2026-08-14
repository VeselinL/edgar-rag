"""Extract the reviewed byte-exact SEC table slices from the frozen raw corpus.

This utility is intentionally test-only.  It never edits ``data/raw`` and will
not replace an existing fixture unless ``--overwrite`` is supplied.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from xml.parsers import expat


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = Path(__file__).resolve().parent

# Values are the persisted one-based HTML table indexes.  The corresponding
# zero-based indexes are recorded independently in manifest.json.
TABLES = {
    "APTV": (13, 86, 87, 88, 96),
    "AUR": (20, 21, 34),
    "F": (115,),
    "GM": (9, 10, 18),
    "GOOGL": (103, 104, 130),
    "MBLY": (180, 181),
    "NVDA": (19, 21),
    "OUST": (19, 28, 29),
    "QCOM": (58,),
    "TSLA": (38, 39, 40),
}

# Context slices are contiguous raw byte ranges.  They include the selected
# table group plus up to eight complete preceding sibling elements, which is
# enough for the bounded production title/region lookback.  Geometry-only
# fixtures continue to use the isolated table files above.
CONTEXT_GROUPS = {
    "APTV": {"continuation-0086-0088": (86, 87, 88)},
    "AUR": {"continuation-0020-0021": (20, 21), "priority-0034": (34,)},
    "F": {"priority-0115": (115,)},
    "GM": {"priority-0009": (9,), "priority-0018": (18,)},
    "GOOGL": {"continuation-0103-0104": (103, 104), "priority-0130": (130,)},
    "MBLY": {"continuation-0180-0181": (180, 181)},
    "NVDA": {"priority-0021": (21,)},
    "OUST": {"priority-0019": (19,), "continuation-0028-0029": (28, 29)},
    "QCOM": {"priority-0058": (58,)},
    "TSLA": {"priority-0038": (38,), "continuation-0039-0040": (39, 40)},
}

TABLE_TAG = re.compile(br"(?is)<\s*(/?)\s*table\b[^>]*>")


class SourceNode:
    def __init__(self, name: str, start: int, parent: "SourceNode | None"):
        self.name = name.split(":")[-1].casefold()
        self.start = start
        self.end = -1
        self.parent = parent
        self.children: list[SourceNode] = []


def source_tree(raw_html: bytes) -> tuple[SourceNode, dict[int, SourceNode]]:
    """Build a byte-offset tree with Expat without rewriting source bytes."""
    parser = expat.ParserCreate()
    synthetic_root = SourceNode("document", 0, None)
    stack = [synthetic_root]
    tables: dict[int, SourceNode] = {}

    def start(name: str, _attributes: dict) -> None:
        node = SourceNode(name, parser.CurrentByteIndex, stack[-1])
        stack[-1].children.append(node)
        stack.append(node)
        if node.name == "table":
            tables[len(tables) + 1] = node

    def end(_name: str) -> None:
        node = stack.pop()
        closing_start = parser.CurrentByteIndex
        node.end = raw_html.find(b">", closing_start) + 1
        if node.end == 0:
            raise ValueError(f"Could not locate closing tag for {node.name}")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.Parse(raw_html, True)
    return synthetic_root, tables


def contextual_byte_range(
    tables: dict[int, SourceNode], indexes: tuple[int, ...], lookback: int = 8
) -> tuple[int, int]:
    nodes = [tables[index] for index in indexes]
    first_container = nodes[0].parent
    last_container = nodes[-1].parent
    if first_container is None or last_container is None:
        raise ValueError("A table fixture has no source parent")
    # Preserve complete native parents so captions represented as sibling
    # spans/paragraphs remain structurally visible in the fixture.
    if first_container is not last_container and first_container.parent is not last_container.parent:
        # Ascend each side until the selected containers are siblings.  This is
        # bounded by the source tree and never changes the selected bytes.
        left_ancestors = []
        value = first_container
        while value is not None:
            left_ancestors.append(value)
            value = value.parent
        right_ancestors = set()
        value = last_container
        while value is not None:
            right_ancestors.add(value)
            value = value.parent
        common = next(node for node in left_ancestors if node in right_ancestors)
        while first_container.parent is not common:
            first_container = first_container.parent
        while last_container.parent is not common:
            last_container = last_container.parent

    siblings = first_container.parent.children
    first_position = siblings.index(first_container)
    start_node = siblings[max(0, first_position - lookback)]
    return start_node.start, last_container.end


def table_byte_ranges(raw_html: bytes) -> dict[int, tuple[int, int]]:
    """Return one-based preorder table ranges, including exact source bytes."""
    ranges = []
    stack = []
    ordinal = 0
    for match in TABLE_TAG.finditer(raw_html):
        if not match.group(1):
            ordinal += 1
            stack.append((ordinal, match.start()))
        elif stack:
            index, start = stack.pop()
            ranges.append((index, start, match.end()))
    if stack:
        raise ValueError("Frozen raw filing contains an unterminated table")
    return {index: (start, end) for index, start, end in ranges}


def extract(*, overwrite: bool = False) -> None:
    for ticker, indexes in TABLES.items():
        raw_paths = sorted((PROJECT_ROOT / "data" / "raw" / ticker).glob("*-10-K.html"))
        if len(raw_paths) != 1:
            raise ValueError(f"Expected one frozen raw filing for {ticker}, found {raw_paths}")
        raw_html = raw_paths[0].read_bytes()
        ranges = table_byte_ranges(raw_html)
        _, source_tables = source_tree(raw_html)
        output_directory = FIXTURE_ROOT / ticker
        output_directory.mkdir(parents=True, exist_ok=True)
        for index in indexes:
            try:
                start, end = ranges[index]
            except KeyError as exc:
                raise ValueError(f"Raw table {ticker} #{index} does not exist") from exc
            output_path = output_directory / f"table-{index:04d}.html"
            if output_path.exists() and not overwrite:
                if output_path.read_bytes() != raw_html[start:end]:
                    raise FileExistsError(f"Fixture differs from frozen source: {output_path}")
                continue
            output_path.write_bytes(raw_html[start:end])
        for name, context_indexes in CONTEXT_GROUPS.get(ticker, {}).items():
            start, end = contextual_byte_range(source_tables, context_indexes)
            output_path = output_directory / f"context-{name}.html"
            if output_path.exists() and not overwrite:
                if output_path.read_bytes() != raw_html[start:end]:
                    raise FileExistsError(f"Context fixture differs from frozen source: {output_path}")
                continue
            output_path.write_bytes(raw_html[start:end])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()
    extract(overwrite=arguments.overwrite)


if __name__ == "__main__":
    main()
