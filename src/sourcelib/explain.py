"""A structural digest of a page, so a spec can be written without reading the whole thing.

A novel page is routinely several hundred kilobytes, nearly all of it navigation, scripts and
advertising. Writing a selector from that means scrolling; writing one from a few kilobytes of
"here is what repeats, here is what carries the data, here is the heading" is reading.

What it reports is chosen from where specs actually get their values: page metadata, a data
script, one heading, a lazily-loaded cover, and a group of links that repeats. The last is the
important one, because the chapter list is the field most often got wrong and the digest can
say how many rows a candidate selector would match before anything is written.
"""

from __future__ import annotations

import json as jsonlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from bs4 import Tag

from sourcelib.spec.extract import Document

__all__ = ["Digest", "Group", "explain", "format_digest"]

#: How many rows a group needs before it looks like a list rather than a coincidence.
MIN_GROUP = 3

#: Attributes a lazily-loaded image hides its real source in.
LAZY = ("data-lazy-src", "data-src", "data-original", "srcset", "src")

#: Enough of a value to recognise, short enough to scan.
SNIP = 90

_NUMERIC = re.compile(r"^\s*\d+\s*$")
_NEXTISH = re.compile(r"(?i)\b(next|older|newer|more|»|›|下一[页頁章])\b")


@dataclass
class Group:
    """A repeated structure: what selects it, how many rows, and what a row holds."""

    selector: str
    rows: int
    row_selector: str = ""
    link_selector: str = ""
    sample_text: str = ""
    sample_href: str = ""
    attributes: List[str] = field(default_factory=list)


@dataclass
class Digest:
    """What one page offers a spec author."""

    url: str = ""
    bytes: int = 0
    lang: str = ""
    title: str = ""
    meta: Dict[str, str] = field(default_factory=dict)
    json_ld: List[str] = field(default_factory=list)
    data_scripts: List[Dict[str, Any]] = field(default_factory=list)
    headings: List[Dict[str, str]] = field(default_factory=list)
    covers: List[Dict[str, str]] = field(default_factory=list)
    groups: List[Group] = field(default_factory=list)
    pagination: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _snip(text: Optional[str]) -> str:
    if not text:
        return ""
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= SNIP else collapsed[: SNIP - 1] + "…"


def selector_for(node: Tag) -> str:
    """A short, stable selector for *node*: its id if it has one, else tag plus classes."""
    node_id = node.get("id")
    if node_id and isinstance(node_id, str) and not _looks_generated(node_id):
        return f"{node.name}#{node_id}"

    classes = [c for c in (node.get("class") or []) if isinstance(c, str)]
    stable = [c for c in classes if not _looks_generated(c)][:2]
    if stable:
        return node.name + "".join(f".{c}" for c in stable)
    return node.name


def _looks_generated(value: str) -> bool:
    """Whether a class or id looks build-generated and so unsafe to select on."""
    if len(value) > 24:
        return True
    # A hash-like tail, as emitted by CSS-in-JS and bundlers.
    return bool(re.search(r"[-_][0-9a-f]{5,}$", value)) or bool(
        re.fullmatch(r"[0-9a-f]{8,}", value)
    )


def _signature(node: Tag) -> Tuple[str, str]:
    """What makes two sibling rows "the same shape"."""
    classes = tuple(sorted(c for c in (node.get("class") or []) if isinstance(c, str)))
    return node.name, ".".join(classes)


def _metadata(document: Document) -> Tuple[Dict[str, str], List[str], str, str]:
    node = document.node
    if node is None:
        return {}, [], "", ""

    meta: Dict[str, str] = {}
    for tag in node.select("meta[property], meta[name]"):
        key = str(tag.get("property") or tag.get("name") or "")
        if key.startswith(("og:", "twitter:")) or key in ("description", "author", "keywords"):
            content = tag.get("content")
            if content:
                meta[key] = _snip(str(content))

    ld: List[str] = []
    for script in node.select('script[type="application/ld+json"]'):
        try:
            data = jsonlib.loads(script.get_text())
        except ValueError:
            continue
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if isinstance(entry, dict):
                ld.extend(sorted(entry)[:12])

    html = node.select_one("html[lang]")
    lang = str(html.get("lang")) if html else ""
    title = node.select_one("title")
    return meta, sorted(set(ld)), lang, _snip(title.get_text() if title else "")


def _data_scripts(document: Document) -> List[Dict[str, Any]]:
    """Scripts carrying structured data, and the top-level keys a `json` path would start at."""
    node = document.node
    if node is None:
        return []

    found: List[Dict[str, Any]] = []
    for script in node.select("script"):
        text = script.get_text().strip()
        if not text or len(text) < 16:
            continue

        script_id = script.get("id")
        script_type = script.get("type")
        parsed = _as_json(text)
        if parsed is None and not script_id:
            continue

        entry: Dict[str, Any] = {}
        if script_id:
            entry["selector"] = f"script#{script_id}"
        elif script_type:
            entry["selector"] = f'script[type="{script_type}"]'
        else:
            continue

        entry["bytes"] = len(text)
        if isinstance(parsed, dict):
            entry["keys"] = sorted(parsed)[:12]
        elif isinstance(parsed, list):
            entry["keys"] = ["$ is an array"]
            if parsed and isinstance(parsed[0], dict):
                entry["row_keys"] = sorted(parsed[0])[:12]
        else:
            # Not JSON: a JavaScript literal, which needs a regex rather than a json path.
            entry["keys"] = []
            entry["note"] = "not JSON; read it with regex"
        found.append(entry)
    return found[:6]


def _as_json(text: str) -> Any:
    try:
        return jsonlib.loads(text)
    except ValueError:
        return None


def _headings(document: Document) -> List[Dict[str, str]]:
    node = document.node
    if node is None:
        return []
    out = []
    for tag in node.select("h1, h2")[:6]:
        text = _snip(tag.get_text())
        if text:
            out.append({"selector": selector_for(tag), "text": text})
    return out


def _covers(document: Document) -> List[Dict[str, str]]:
    """Images that look like a cover: lazily loaded, or beside a heading."""
    node = document.node
    if node is None:
        return []

    out: List[Dict[str, str]] = []
    for image in node.select("img")[:40]:
        present = [a for a in LAZY if image.get(a)]
        lazy = [a for a in present if a != "src"]
        parent = image.parent
        near_heading = bool(parent and parent.name in ("figure", "header", "div") and lazy)
        if not lazy and not near_heading:
            continue
        where = selector_for(parent) + " img" if isinstance(parent, Tag) else "img"
        out.append(
            {
                "selector": where,
                "attr": ", ".join(present),
                "value": _snip(str(image.get(present[0]) if present else "")),
            }
        )
        if len(out) >= 4:
            break
    return out


def _groups(document: Document) -> List[Group]:
    """Repeated sibling structures containing links: the chapter-list detector."""
    node = document.node
    if node is None:
        return []

    candidates: List[Group] = []
    for container in node.find_all(True):
        children = [c for c in container.children if isinstance(c, Tag)]
        if len(children) < MIN_GROUP:
            continue

        counts: Dict[Tuple[str, str], List[Tag]] = {}
        for child in children:
            counts.setdefault(_signature(child), []).append(child)

        signature, rows = max(counts.items(), key=lambda item: len(item[1]))
        if len(rows) < MIN_GROUP or len(rows) < len(children) / 2:
            continue

        linked = [r for r in rows if r.name == "a" or r.find("a", href=True)]
        if len(linked) < MIN_GROUP:
            continue

        first = linked[0]
        anchor = first if first.name == "a" else first.find("a", href=True)
        row_selector = signature[0] + "".join(f".{c}" for c in signature[1].split(".") if c)
        candidates.append(
            Group(
                selector=f"{selector_for(container)} {row_selector}".strip(),
                rows=len(linked),
                row_selector=row_selector,
                link_selector="" if first.name == "a" else "a",
                sample_text=_snip(anchor.get_text() if anchor else ""),
                sample_href=_snip(str(anchor.get("href")) if anchor else ""),
                attributes=sorted(
                    k for k in (anchor.attrs if anchor else {}) if k not in ("href", "class")
                )[:6],
            )
        )

    candidates.sort(key=lambda g: g.rows, reverse=True)
    return _distinct(candidates)[:5]


def _distinct(groups: List[Group]) -> List[Group]:
    seen = set()
    out = []
    for group in groups:
        if group.selector in seen:
            continue
        seen.add(group.selector)
        out.append(group)
    return out


def _pagination(document: Document) -> List[Dict[str, Any]]:
    """Link groups that look like a pager, which is where a page count comes from."""
    node = document.node
    if node is None:
        return []

    out: List[Dict[str, Any]] = []
    for container in node.find_all(True):
        links = [a for a in container.find_all("a", recursive=False) if a.get_text().strip()]
        if len(links) < 2:
            continue
        numbers = [a for a in links if _NUMERIC.match(a.get_text())]
        nextish = [a for a in links if _NEXTISH.search(a.get_text())]
        if not numbers and not nextish:
            continue
        entry: Dict[str, Any] = {
            "selector": f"{selector_for(container)} a",
            "numeric": len(numbers),
        }
        if numbers:
            entry["highest"] = max(int(a.get_text().strip()) for a in numbers)
            entry["hint"] = "count: { all: true, pipe: [max] }"
        if nextish:
            entry["next_text"] = _snip(nextish[0].get_text())
            entry["hint"] = entry.get("hint") or "next: { attr: href }"
        out.append(entry)
        if len(out) >= 3:
            break
    return out


def explain(document: Document) -> Digest:
    """Everything the digest reports about one already-fetched document."""
    meta, ld, lang, title = _metadata(document)
    return Digest(
        url=document.url,
        bytes=len(document.text or ""),
        lang=lang,
        title=title,
        meta=meta,
        json_ld=ld,
        data_scripts=_data_scripts(document),
        headings=_headings(document),
        covers=_covers(document),
        groups=_groups(document),
        pagination=_pagination(document),
    )


def format_digest(digest: Digest) -> str:
    """The human-readable form, written to be short enough to read in one screen."""
    out: List[str] = [
        f"{digest.url}",
        f"  {digest.bytes:,} bytes" + (f", lang={digest.lang}" if digest.lang else ""),
    ]

    if digest.title:
        out += ["", "TITLE TAG", f"  {digest.title}"]

    if digest.meta or digest.json_ld:
        out += ["", "METADATA  (the interpreter falls back to these, so a spec may omit the field)"]
        for key, value in sorted(digest.meta.items()):
            out.append(f"  {key:22} {value}")
        if digest.json_ld:
            out.append(f"  {'ld+json keys':22} {', '.join(digest.json_ld)}")

    if digest.data_scripts:
        out += ["", "DATA SCRIPTS  (css selects the script, json reads into it)"]
        for entry in digest.data_scripts:
            out.append(f"  {entry['selector']}  ({entry['bytes']:,} bytes)")
            if entry.get("keys"):
                out.append(f"      keys: {', '.join(entry['keys'])}")
            if entry.get("row_keys"):
                out.append(f"      row keys: {', '.join(entry['row_keys'])}")
            if entry.get("note"):
                out.append(f"      {entry['note']}")

    if digest.headings:
        out += ["", "HEADINGS"]
        for heading in digest.headings:
            out.append(f"  {heading['selector']:30} {heading['text']}")

    if digest.covers:
        out += ["", "LIKELY COVER"]
        for cover in digest.covers:
            out.append(f"  {cover['selector']:30} attr: {cover['attr']}")
            out.append(f"      {cover['value']}")

    if digest.groups:
        out += ["", "REPEATED STRUCTURES  (the chapter list is usually the largest)"]
        for group in digest.groups:
            out.append(f"  {group.rows:>5} rows  {group.selector}")
            if group.sample_text:
                out.append(f"           text: {group.sample_text}")
            if group.sample_href:
                out.append(f"           href: {group.sample_href}")
            if group.attributes:
                out.append(f"           other attributes: {', '.join(group.attributes)}")

    if digest.pagination:
        out += ["", "PAGINATION"]
        for entry in digest.pagination:
            out.append(f"  {entry['selector']}")
            if entry.get("highest"):
                out.append(f"      {entry['numeric']} numeric links, highest {entry['highest']}")
            if entry.get("next_text"):
                out.append(f"      next-looking link: {entry['next_text']}")
            if entry.get("hint"):
                out.append(f"      {entry['hint']}")

    if not digest.groups:
        out += [
            "",
            "  No repeated structure found. The chapter list may be built by scripts,",
            "  in which case try `render: true`, or it may live behind another request.",
        ]

    return "\n".join(out)
