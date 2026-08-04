"""Reading a value out of a document, per RFC-0001 sections 3.4, 4.1, 4.3 and 6.4.

The evaluation order is normative rather than incidental: ``all`` resolves before ``pipe`` so
a pipe maps element-wise over a list, and reversing those two silently changes what a
list-valued field with a text pipe means.
"""

from __future__ import annotations

import json as jsonlib
import re
import warnings
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

from sourcelib.spec.model import Extractor
from sourcelib.transform import DEFAULT_PARSER, REGISTRY, apply_pipe

__all__ = [
    "DEFAULT_PARSER",
    "Document",
    "ExtractError",
    "default_pipe",
    "extract",
    "read_json_path",
]

#: Inline wrappers the body and synopsis defaults flatten. They carry no meaning in prose, and
#: keeping them would leave layout spans and donation links in the text.
INLINE_WRAPPERS: Tuple[str, ...] = ("a", "abbr", "acronym", "label", "span", "time")

#: RFC-0001 section 6.4. The most specific entry wins, so an ItemList field named `url` takes
#: the URL default rather than the general one.
_DEFAULT_PIPES: Dict[str, List[Any]] = {
    "tags": ["trim", "collapse_spaces", "drop_empty", "unique"],
    "synopsis": [{"unwrap": list(INLINE_WRAPPERS)}, "paragraphs"],
    "body": [{"unwrap": list(INLINE_WRAPPERS)}, "paragraphs"],
    "url": ["trim"],
    "cover": ["trim"],
}

_GENERAL_DEFAULT: List[Any] = ["trim", "collapse_spaces"]

#: Field kinds whose value is a URL and therefore resolved against the document (section 4.3).
URL_FIELDS: Tuple[str, ...] = ("url", "cover")

#: Steps that consume a node, read from the registry rather than listed here. Every step
#: already declares what it takes, so a second list can only drift from it: hand-written, this
#: held five of the eleven, and a pipe beginning with one of the six it missed had its element
#: flattened to a string before the step ran.
#:
#: A pipe starting with one of these keeps the selected element instead of `attr`'s default
#: (§3.4), and a default built from them gets `parse_html` in front of a string, which is what
#: lets a body arrive inside a JSON field with no boilerplate.
_NODE_STEPS = frozenset(name for name, step in REGISTRY.items() if step.takes == "node")


class ExtractError(Exception):
    """An extractor could not be evaluated: a bad path, or a source combination refused."""


class Document:
    """A fetched document and what an extractor needs to know about it."""

    __slots__ = ("url", "node", "text", "headers", "_parsed")

    def __init__(
        self,
        url: str = "",
        node: Optional[Tag] = None,
        text: str = "",
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.url = url
        self.node = node
        self.text = text
        # Header lookup is case-insensitive, as HTTP requires.
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        self._parsed: Any = None

    @classmethod
    def from_html(
        cls,
        markup: str,
        url: str = "",
        parser: str = DEFAULT_PARSER,
        headers: Optional[Mapping[str, str]] = None,
    ) -> "Document":
        with warnings.catch_warnings():
            # An Atom or RSS feed is a legitimate source document, and lxml reads one correctly:
            # this warning only advises using an XML parser instead. Suppressed because it would
            # otherwise print a paragraph to stderr on every page a feed-backed source fetches.
            warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
            node = BeautifulSoup(markup, parser)
        return cls(url=url, node=node, text=markup, headers=headers)

    @classmethod
    def from_json(
        cls, payload: Any, url: str = "", headers: Optional[Mapping[str, str]] = None
    ) -> "Document":
        document = cls(url=url, text=jsonlib.dumps(payload), headers=headers)
        document._parsed = payload
        return document

    @property
    def parsed(self) -> Any:
        """The body decoded as JSON, or None when it is not JSON."""
        if self._parsed is None and self.text:
            try:
                self._parsed = jsonlib.loads(self.text)
            except ValueError:
                self._parsed = None
        return self._parsed


def read_json_path(data: Any, path: str) -> Any:
    """Read a dotted path out of already-parsed JSON.

    ``$`` denotes the whole body, which is how an API returning a bare array at the top level
    is selected from. A segment that is all digits indexes a list.
    """
    if path in ("", "$"):
        return data

    current = data
    for segment in path.split("."):
        if segment == "$" or current is None:
            continue
        if isinstance(current, Mapping):
            current = current.get(segment)
        elif isinstance(current, (list, tuple)):
            if not segment.lstrip("-").isdigit():
                return None
            index = int(segment)
            current = current[index] if -len(current) <= index < len(current) else None
        else:
            return None
    return current


def default_pipe(kind: Optional[str], value_is_text: bool) -> List[Any]:
    """The pipe a field of this kind gets when the spec declares none.

    *value_is_text* prepends ``parse_html`` to a node-consuming default, so a synopsis or body
    extracted from a JSON string is parsed before a node step runs on it.
    """
    if kind is None:
        return list(_GENERAL_DEFAULT)
    steps = list(_DEFAULT_PIPES.get(kind, _GENERAL_DEFAULT))
    if value_is_text and _consumes_node(steps):
        return ["parse_html", *steps]
    return steps


def _consumes_node(steps: Sequence[Any]) -> bool:
    if not steps:
        return False
    first = steps[0]
    name = first if isinstance(first, str) else next(iter(first), "")
    return name in _NODE_STEPS


def _pipe_wants_node(
    declared: Optional[Sequence[Any]],
    kind: Optional[str],
    pipes: Optional[Mapping[str, Any]],
) -> bool:
    """Whether the effective pipe's first step consumes a node."""
    steps = declared if declared is not None else _DEFAULT_PIPES.get(kind or "", None)
    if steps is None:
        return False
    # A pipe named in `pipes` expands, so its own first step is what decides.
    if steps and isinstance(steps[0], str) and pipes and steps[0] in pipes:
        return _consumes_node(list(pipes[steps[0]]))
    return _consumes_node(list(steps))


def _select(document: Document, selector: str, want_all: bool) -> Union[List[Tag], Optional[Tag]]:
    if document.node is None:
        raise ExtractError("a css selector needs a parsed document")
    if want_all:
        return document.node.select(selector)
    return document.node.select_one(selector)


def _attribute(node: Any, names: Union[str, Sequence[str]]) -> Any:
    """Read `text`, `html`, `outer_html` or the first present attribute."""
    wanted = [names] if isinstance(names, str) else list(names)
    if not isinstance(node, Tag):
        return node

    for name in wanted:
        if name == "text":
            return node.get_text()
        if name == "html":
            return node.decode_contents()
        if name == "outer_html":
            return str(node)
        value = node.get(name)
        if value is not None:
            return " ".join(value) if isinstance(value, list) else value
    return None


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def _resolve_source(spec: Extractor, document: Document, scope: Any) -> Any:
    """Step 1 of section 4.1: produce the raw value or node, before `all` and `attr`."""
    if spec.const is not None:
        return spec.const

    if spec.header is not None:
        return document.headers.get(spec.header.lower())

    node: Any = scope if scope is not None else document.node

    if spec.css is not None:
        selected = (
            _select(document, spec.css, spec.all)
            if scope is None
            else _select_within(scope, spec.css, spec.all)
        )
        node = selected
        if node is None or (isinstance(node, list) and not node):
            return [] if spec.all else None

    if spec.json_ is not None:
        return _read_json(node, spec, document)

    if spec.regex is not None:
        return _read_regex(node, spec, document)

    return node


def _select_within(scope: Any, selector: str, want_all: bool) -> Any:
    if not isinstance(scope, Tag):
        raise ExtractError("a css selector needs a node in scope")
    return scope.select(selector) if want_all else scope.select_one(selector)


def _read_json(node: Any, spec: Extractor, document: Document) -> Any:
    path = spec.json_ or ""
    if spec.css is not None:
        # The selector named which script carries the data; read inside it.
        nodes = node if isinstance(node, list) else [node]
        values = []
        for one in nodes:
            if one is None:
                continue
            try:
                values.append(read_json_path(jsonlib.loads(one.get_text()), path))
            except ValueError:
                values.append(None)
        return values if spec.all else (values[0] if values else None)

    data = document.parsed if not isinstance(node, (dict, list)) else node
    return read_json_path(data, path)


def _read_regex(node: Any, spec: Extractor, document: Document) -> Any:
    pattern = re.compile(spec.regex or "")

    def read(text: str) -> Optional[str]:
        match = pattern.search(text)
        if match is None:
            return None
        return match.group(1) if pattern.groups else match.group(0)

    if spec.css is not None:
        nodes = node if isinstance(node, list) else [node]
        values = [read(n.get_text()) for n in nodes if n is not None]
        return values if spec.all else (values[0] if values else None)

    # The document's *raw* text, not its extracted text: a pattern often reads an attribute
    # or a JavaScript literal, neither of which survives get_text().
    source = str(node) if isinstance(node, Tag) else document.text
    if spec.all:
        return [m.group(1) if pattern.groups else m.group(0) for m in pattern.finditer(source)]
    return read(source)


def extract(
    spec: Union[Extractor, str],
    document: Document,
    scope: Any = None,
    kind: Optional[str] = None,
    pipes: Optional[Mapping[str, Any]] = None,
) -> Any:
    """Evaluate *spec* against *document*, in the order section 4.1 fixes.

    ``source``, then ``all``, then ``attr``, then ``pipe``, then ``default``, then each
    ``fallback`` in turn. *kind* selects the default pipe and whether the value is a URL.
    """
    if isinstance(spec, str):
        raise ExtractError("a template-valued field is rendered, not extracted")

    value = _resolve_source(spec, document, scope)

    declared = spec.pipe
    if isinstance(declared, str):
        declared = [declared]

    # An undeclared `attr` yields whatever the pipe consumes, so a node reaches a node step
    # rather than being flattened to a string first (section 3.4). Without this a chapter body
    # loses every paragraph boundary before `paragraphs` runs.
    wants_node = _pipe_wants_node(declared, kind, pipes)
    attr_declared = "attr" in spec.model_fields_set

    if spec.const is None and spec.header is None and (attr_declared or not wants_node):
        if isinstance(value, list):
            value = [_attribute(v, spec.attr) for v in value]
        else:
            value = _attribute(value, spec.attr)

    steps = declared if declared is not None else default_pipe(kind, _looks_like_text(value))

    if not _is_empty(value) or spec.const is not None:
        value = apply_pipe(value, list(steps), dict(pipes or {}))

    if _is_empty(value) and spec.default is not None:
        value = spec.default

    if _is_empty(value):
        for alternative in spec.fallback:
            value = extract(alternative, document, scope, kind, pipes)
            if not _is_empty(value):
                break

    if kind in URL_FIELDS and document.url:
        value = _absolutise(value, document.url)

    return value


def _looks_like_text(value: Any) -> bool:
    if isinstance(value, str):
        return True
    if isinstance(value, list):
        return bool(value) and all(isinstance(v, str) for v in value)
    return False


def _absolutise(value: Any, base: str) -> Any:
    """Resolve against the document's own URL, never against base_url (section 4.3)."""
    if isinstance(value, str) and value:
        return urljoin(base, value)
    if isinstance(value, list):
        return [urljoin(base, v) if isinstance(v, str) and v else v for v in value]
    return value
