"""Reading a repeated structure, per RFC-0001 section 3.8.

Three rules here each remove a hook, and each is easy to implement in a way that looks right
and loses data.

An item whose required field resolves empty is **skipped, not emitted**, which is what lets a
selector be written loosely and narrowed with a pipe instead of hunting a perfect one.

Volume assignment is positional in **document** order, so it happens before any sorting. A
heading row partitions the chapters that follow it, which is the shape real sites use.

And a field may be a URL template over its earlier siblings, so a listing exposing identifiers
rather than links still produces one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from bs4 import Tag

from sourcelib.interpolate import render
from sourcelib.spec.extract import Document, extract, read_json_path
from sourcelib.spec.model import Extractor, ItemList

__all__ = ["Row", "assign_volumes", "group_by_size", "read_items", "read_rows"]


class Row:
    """One item, with the keys its stage names and anything extra the spec captured."""

    __slots__ = ("fields", "node", "order")

    def __init__(self, fields: Dict[str, Any], node: Any = None, order: int = 0) -> None:
        self.fields = fields
        self.node = node
        self.order = order

    def get(self, name: str, default: Any = None) -> Any:
        return self.fields.get(name, default)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Row({self.fields!r})"


def _containers(item_list: ItemList, document: Document) -> List[Any]:
    """The row containers, from a selector or a JSON path."""
    if item_list.css is not None:
        if document.node is None:
            return []
        return list(document.node.select(item_list.css))
    if item_list.json_ is not None:
        data = read_json_path(document.parsed, item_list.json_)
        if isinstance(data, list):
            return list(data)
        return [data] if data is not None else []
    return []


def read_rows(
    item_list: ItemList,
    document: Document,
    required: Sequence[str] = (),
    kinds: Optional[Mapping[str, str]] = None,
    pipes: Optional[Mapping[str, Any]] = None,
    start: int = 0,
) -> Tuple[List[Row], int]:
    """Every row *item_list* yields, and how many were skipped.

    *required* names the fields an item cannot do without. A row missing one disappears, which
    is how navigation links and a "latest chapters" panel stay out of a chapter list.

    `require` on the list adds to that, which is how a row is rejected on a field its stage does
    not need: the field's own pipe decides, since a filter step yields nothing when it rejects.
    """
    kinds = dict(kinds or {})
    needed = (*required, *item_list.require)
    rows: List[Row] = []
    skipped = 0

    for index, container in enumerate(_containers(item_list, document)):
        scope = container if isinstance(container, Tag) else None
        json_scope = None if isinstance(container, Tag) else container

        fields: Dict[str, Any] = {}
        for name, spec in item_list.fields.items():
            fields[name] = _read_field(
                spec, document, scope, json_scope, name, kinds, pipes, fields
            )

        if any(_is_blank(fields.get(name)) for name in needed):
            skipped += 1
            continue

        rows.append(Row(fields, node=scope, order=start + index))

    return rows, skipped


def _read_field(
    spec: Any,
    document: Document,
    scope: Any,
    json_scope: Any,
    name: str,
    kinds: Mapping[str, str],
    pipes: Optional[Mapping[str, Any]],
    so_far: Mapping[str, Any],
) -> Any:
    """One field of one row. A string field is a template over its earlier siblings."""
    if isinstance(spec, str):
        # Fields are evaluated in declaration order, so a template may name any field
        # declared before it.
        return render(spec, {"origin": _origin_of(document), "item": dict(so_far), "vars": {}})

    if json_scope is not None:
        return _read_json_field(spec, json_scope, document, name, kinds, pipes)

    return extract(spec, document, scope=scope, kind=kinds.get(name), pipes=pipes)


def _read_json_field(
    spec: Extractor,
    row: Any,
    document: Document,
    name: str,
    kinds: Mapping[str, str],
    pipes: Optional[Mapping[str, Any]],
) -> Any:
    """A row that is a JSON object rather than an element."""
    if spec.json_ is not None:
        value: Any = read_json_path(row, spec.json_)
    elif spec.const is not None:
        value = spec.const
    else:
        value = row
    inner = Document.from_json(value, url=document.url, headers=document.headers)
    return extract(
        Extractor.model_validate(
            {
                "json": "$",
                "pipe": spec.pipe,
                "default": spec.default,
                "all": spec.all,
            }
        ),
        inner,
        kind=kinds.get(name),
        pipes=pipes,
    )


def _origin_of(document: Document) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(document.url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme else ""


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _document_order(document: Document, *groups: Sequence[Any]) -> List[Tuple[int, Any]]:
    """Tag positions across several selections, in the order they appear in the document."""
    wanted: Dict[int, int] = {}
    for group_index, group in enumerate(groups):
        for element in group:
            if isinstance(element, Tag):
                wanted[id(element)] = group_index

    if document.node is None:
        return []

    ordered: List[Tuple[int, Any]] = []
    for element in document.node.find_all(True):
        group_index = wanted.get(id(element))
        if group_index is not None:
            ordered.append((group_index, element))
    return ordered


def assign_volumes(
    document: Document,
    chapters: ItemList,
    volumes: ItemList,
    chapter_rows: Sequence[Row],
    volume_titles: Sequence[Row],
) -> Dict[int, str]:
    """Give every chapter row a volume, by the nearest preceding heading.

    Both selectors run over the same container, so the two selections are interleaved in
    document order. Chapters before the first heading belong to an implicit first volume.
    """
    heading_rows = {id(row.node): row for row in volume_titles if row.node is not None}
    chapter_nodes = {id(row.node): row for row in chapter_rows if row.node is not None}
    if not heading_rows:
        return {}

    titles: Dict[int, str] = {}
    current = 0
    for group_index, element in _document_order(
        document,
        [row.node for row in volume_titles if row.node is not None],
        [row.node for row in chapter_rows if row.node is not None],
    ):
        if group_index == 0:
            current += 1
            heading = heading_rows.get(id(element))
            titles[current] = str(heading.get("title") or "") if heading else ""
            continue
        row = chapter_nodes.get(id(element))
        # A `volume` field on the row itself wins over position (section 3.8).
        if row is not None and row.get("volume") is None:
            row.fields["volume"] = max(current, 1)

    # Chapters before the first heading belong to an implicit first volume.
    for row in chapter_rows:
        if row.get("volume") is None:
            row.fields["volume"] = 1
    return titles


def group_by_size(rows: Sequence[Row], per_volume: int) -> None:
    """Number volumes every *per_volume* chapters, which is what most sources rely on."""
    for index, row in enumerate(rows):
        if row.get("volume") is None:
            row.fields["volume"] = 1 + index // max(1, per_volume)


def read_items(
    item_list: ItemList,
    document: Document,
    required: Sequence[str] = (),
    kinds: Optional[Mapping[str, str]] = None,
    pipes: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[Row], int]:
    """Rows, sorted and reversed as declared. Sorting runs after volume assignment."""
    rows, skipped = read_rows(item_list, document, required, kinds, pipes)
    return sort_rows(rows, item_list), skipped


def sort_rows(rows: List[Row], item_list: ItemList) -> List[Row]:
    """Order rows by `sort_by`, numerically, then apply `reverse`.

    A row whose value does not parse as a number sorts after every row that does, keeping its
    relative position among the others. Numeric only because the field exists for chapter and
    volume numbers, and inferring the comparison would let one malformed row change how every
    other row compares.
    """
    ordered = list(rows)
    if item_list.sort_by:
        name = item_list.sort_by

        def key(row: Row) -> Tuple[int, float, int]:
            number = _as_number(row.get(name))
            if number is None:
                return (1, 0.0, row.order)
            return (0, number, row.order)

        ordered.sort(key=key)
    if item_list.reverse:
        ordered.reverse()
    return ordered


def _as_number(value: Any) -> Optional[float]:
    import re

    if value is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None
