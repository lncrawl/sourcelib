"""The transform registry, per RFC-0001 section 6.

Cleaning is an operation rather than a place, so any extracted value may carry a pipe and
there is no separate cleaner.

Three rules from the RFC shape everything here.

Steps are typed, and a pipe whose types do not connect is rejected at validation time rather
than mid-crawl (section 6.1). A scalar step applied to a list runs element-wise, which is why
the format needs no ``map`` construct (section 6.3). And a step with nothing to do never
raises: a *filter* yields nothing, a *cleanup* yields its input unchanged (section 6.2). That
last distinction is the one that loses data when it is wrong, because a cleanup that emptied
its value would delete rows while the crawl still reported success.
"""

from __future__ import annotations

import re
import unicodedata
import warnings
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple, Union

from bs4 import BeautifulSoup, Comment, MarkupResemblesLocatorWarning, Tag
from bs4.element import NavigableString

__all__ = [
    "DEFAULT_PARSER",
    "REGISTRY",
    "Kind",
    "StepError",
    "StepSpec",
    "apply_pipe",
    "apply_step",
    "expand_pipe",
    "parse_step",
    "validate_pipe",
]

#: The parser a spec gets when it declares none. `lxml` is faster than the standard library's and
#: recovers from real-world markup better, which matters because a selector that silently matches
#: nothing is the most common defect in a source.
#:
#: A default, not a rule. Some pages are lxml-hostile, and `parser:` on a spec is how one of those
#: asks for `html.parser` instead.
DEFAULT_PARSER = "lxml"

Kind = str

#: What a step consumes or produces. `any` connects to anything, which only `hook` needs.
NODE, HTML, TEXT, LIST, ANY = "node", "html", "text", "list", "any"

#: Block elements that end a paragraph in `paragraphs`.
DEFAULT_BLOCK_TAGS: Tuple[str, ...] = (
    "article",
    "aside",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "main",
    "p",
    "section",
)

#: Elements `paragraphs` emits whole, with attributes, without descending into them.
DEFAULT_PRESERVE: Tuple[str, ...] = ("img", "pre", "canvas")

#: Deferred image attributes, in the order `unlazy_images` tries them. Reversing this picks
#: the placeholder the site is deferring away from.
LAZY_ATTRS: Tuple[str, ...] = ("data-lazy-src", "data-src", "src")

#: Elements a parser adds around a fragment, which `_blocks_of` descends through. Not any single
#: wrapper: descending through a lone `<div>` the site actually wrote would change what the spec
#: selected.
_PARSER_WRAPPERS: FrozenSet[str] = frozenset({"html", "body"})

#: Steps that yield nothing when they do not match. Everything else passes its value through.
FILTERS: FrozenSet[str] = frozenset({"regex", "reject"})


class StepError(Exception):
    """A pipe is malformed: an unknown step, a bad argument, or types that do not connect."""


class StepSpec:
    """One registry entry: what it consumes, what it produces, and how."""

    __slots__ = ("name", "takes", "gives", "fn")

    def __init__(self, name: str, takes: Kind, gives: Kind, fn: Callable[..., Any]) -> None:
        self.name = name
        self.takes = takes
        self.gives = gives
        self.fn = fn

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"StepSpec({self.name!r}, {self.takes} -> {self.gives})"


# --------------------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------------------- #


def _as_tag(value: Any) -> Tag:
    if isinstance(value, Tag):
        return value
    raise StepError(f"expected a node, got {type(value).__name__}")


def _carries_content(fragment: str) -> bool:
    """Whether a serialised fragment holds readable text or an image.

    A paragraph made only of markup is dropped, but one holding just an `<img>` is kept, which is
    how a manga page survives a text-shaped pipe.

    BeautifulSoup warns when the string it is handed looks like a URL rather than markup, and here
    it often is one: a blogger post whose whole paragraph is a bare link. The library documents
    that warning as spurious for exactly this case, so it is silenced around this parse and
    nowhere else.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
        parsed = BeautifulSoup(fragment, DEFAULT_PARSER)
    return bool(parsed.get_text().strip()) or parsed.find("img") is not None


def _text_of(value: Any) -> str:
    if isinstance(value, Tag):
        return value.get_text()
    return "" if value is None else str(value)


def _names(argument: Any) -> List[str]:
    if isinstance(argument, str):
        return [argument]
    if isinstance(argument, (list, tuple)):
        return [str(a) for a in argument]
    raise StepError(f"expected a name or a list of names, got {type(argument).__name__}")


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


# --------------------------------------------------------------------------------------- #
# node -> node
# --------------------------------------------------------------------------------------- #


def _strip_tags(node: Any, names: Any) -> Tag:
    tag = _as_tag(node)
    for name in _names(names):
        for found in tag.find_all(name):
            found.decompose()
    return tag


def _strip_css(node: Any, selectors: Any) -> Tag:
    tag = _as_tag(node)
    for selector in _names(selectors):
        for found in tag.select(selector):
            found.decompose()
    return tag


def _strip_matching(
    node: Any,
    pattern: str = "",
    tags: Any = None,
) -> Tag:
    """Remove elements whose own text matches *pattern*.

    This is what a translator's note, a watermark and a "read this at ..." line have in common:
    the markup around them is ordinary, so only the words identify them.

    Restricted to elements with no element children unless *tags* names some. Every ancestor of a
    match also contains the matching text, so an unrestricted search would find the chapter body
    itself and delete the chapter while reporting success. Naming tags is the way to reach a
    container deliberately.
    """
    tag = _as_tag(node)
    if not pattern:
        return tag
    wanted = re.compile(pattern)
    names = _names(tags) if tags else None

    for found in tag.find_all(list(names) if names else True):
        if names is None and found.find(True) is not None:
            continue
        if wanted.search(found.get_text()):
            found.decompose()
    return tag


def _unwrap(node: Any, names: Any) -> Tag:
    tag = _as_tag(node)
    for name in _names(names):
        for found in tag.find_all(name):
            found.unwrap()
    return tag


def _unwrap_all(node: Any) -> Tag:
    tag = _as_tag(node)
    for found in tag.find_all(True):
        found.unwrap()
    return tag


def _keep_attrs(node: Any, names: Any) -> Tag:
    keep = set(_names(names))
    tag = _as_tag(node)
    for element in [tag, *tag.find_all(True)]:
        for attribute in list(element.attrs):
            if attribute not in keep:
                del element.attrs[attribute]
    return tag


def _unlazy_images(node: Any) -> Tag:
    tag = _as_tag(node)
    for image in tag.find_all("img"):
        chosen = next(
            (str(image[a]).strip() for a in LAZY_ATTRS if image.get(a) and str(image[a]).strip()),
            None,
        )
        for attribute in LAZY_ATTRS:
            if attribute in image.attrs:
                del image.attrs[attribute]
        if chosen:
            image["src"] = chosen
    return tag


def _drop_empty_nodes(node: Any) -> Tag:
    tag = _as_tag(node)
    for element in tag.find_all(True):
        if element.name == "img" or element.find("img"):
            continue
        if not element.get_text().strip():
            element.decompose()
    return tag


def _content_root(tag: Tag) -> Tag:
    """The element whose children are the real content, past anything the parser added.

    `lxml` wraps every fragment in `<html><body>` while `html.parser` adds nothing, so how deep the
    content sits is the parser's choice and not the markup's. Any step that reads children has to
    skip that scaffolding or it treats it as content: `paragraphs` emitted
    `<p><html><body>text</body></html></p>` for a synopsis that arrived as a JSON string, which is
    the common case rather than an unusual one.
    """
    current = tag
    while True:
        children = [c for c in current.children if isinstance(c, Tag)]
        if len(children) == 1 and children[0].name in _PARSER_WRAPPERS:
            current = children[0]
            continue
        return current


def _blocks_of(tag: Tag) -> List[Tag]:
    """The element children a step should treat as the node's top-level blocks.

    A selected element's blocks are simply its element children. A *parsed* fragment sits deeper,
    and by two amounts that unwind in order: the parser's scaffolding, skipped by name because those
    are the only tags a parser invents, and then one lone wrapper the markup itself had, so
    `<div><p>a</p><p>b</p></div>` yields the two paragraphs and not the div. The second is skipped by
    shape rather than by name, since the site chose that tag.

    Getting either wrong is quiet: the blocks come back as one element whose text is the whole body,
    so `drop_leading` either matches nothing or deletes everything.
    """
    if not isinstance(tag, BeautifulSoup):
        return [c for c in tag.children if isinstance(c, Tag)]

    children = [c for c in _content_root(tag).children if isinstance(c, Tag)]
    if len(children) == 1:
        return [c for c in children[0].children if isinstance(c, Tag)]
    return children


#: The share of a node's text one block may hold and still be taken for a heading. Deliberately
#: high: it is a backstop for the shape the structural test cannot see, a whole body wrapped in one
#: element with no blocks of its own, and a small fragment where a real heading is legitimately a
#: large fraction of very little text must stay removable.
_HEADING_SHARE = 0.9


def _looks_like_a_heading(element: Tag, total: int) -> bool:
    """Whether a matching block is plausibly a heading rather than the body itself.

    Some themes wrap the whole chapter in one element that opens by repeating the title. That block
    matches any heading pattern and holds everything, and removing it deleted a whole chapter while
    the crawl still reported success. `drop_leading` is a cleanup (section 6.2), so when there is
    nothing safe to do it must change nothing.

    A heading is a leaf: it holds a line of text and no blocks of its own. That is the reliable
    signal, because it does not depend on how long the chapter happens to be.
    """
    if element.find(list(DEFAULT_BLOCK_TAGS)) is not None:
        return False
    return not total or len(element.get_text()) < total * _HEADING_SHARE


def _drop_leading(node: Any, matches: Any = None, within: Any = 1) -> Tag:
    tag = _as_tag(node)
    if not matches:
        raise StepError("drop_leading needs a `matches` pattern")
    pattern = re.compile(str(matches))
    total = len(tag.get_text())

    for element in _blocks_of(tag)[: int(within)]:
        if pattern.search(element.get_text()) and _looks_like_a_heading(element, total):
            element.decompose()
            break  # at most one
    return tag


# --------------------------------------------------------------------------------------- #
# node -> html
# --------------------------------------------------------------------------------------- #


def _paragraphs(node: Any, block_tags: Any = None, preserve: Any = None) -> str:
    tag = _as_tag(node)
    blocks = set(_names(block_tags) if block_tags else DEFAULT_BLOCK_TAGS)
    kept = set(_names(preserve) if preserve else DEFAULT_PRESERVE)

    paragraphs: List[str] = []
    current: List[str] = []

    def flush() -> None:
        joined = "".join(current).strip()
        current.clear()
        if not joined:
            return
        if _carries_content(joined):
            paragraphs.append(f"<p>{joined}</p>")

    def walk(parent: Tag) -> None:
        for child in list(parent.children):
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                current.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            if child.name in ("script", "style"):
                continue
            if child.name in kept:
                current.append(str(child))
                continue
            if child.name in blocks or child.name in ("br", "hr"):
                flush()
                if child.name not in ("br", "hr"):
                    walk(child)
                    flush()
                continue
            before = len(current)
            walk(child)
            inner = "".join(current[before:])
            del current[before:]
            if inner.strip():
                current.append(f"<{child.name}>{inner}</{child.name}>")

    walk(_content_root(tag))
    flush()
    return "".join(paragraphs)


def _inner_html(node: Any) -> str:
    # The content root, not the node: on a parsed fragment the node's own contents are the
    # parser's `<html><body>` wrapper rather than the markup that went in.
    return _content_root(_as_tag(node)).decode_contents()


# --------------------------------------------------------------------------------------- #
# text -> node, node -> text
# --------------------------------------------------------------------------------------- #


def _parse_html(value: Any, parser: Any = DEFAULT_PARSER) -> Tag:
    return BeautifulSoup(_text_of(value), str(parser))


def _text(node: Any) -> str:
    return _text_of(node)


# --------------------------------------------------------------------------------------- #
# text -> text
# --------------------------------------------------------------------------------------- #

_WHITESPACE = re.compile(r"[\s ​]+")


def _trim(value: Any) -> str:
    return _text_of(value).strip()


def _collapse_spaces(value: Any) -> str:
    return _WHITESPACE.sub(" ", _text_of(value)).strip()


def _lower(value: Any) -> str:
    return _text_of(value).lower()


def _title_case(value: Any) -> str:
    # The first character of each word only. Python's str.title() lowercases the rest, which
    # destroys an acronym and turns "don't" into "Don'T".
    return " ".join(w[:1].upper() + w[1:] if w else w for w in _text_of(value).split(" "))


def _normalize_unicode(value: Any, form: Any = "NFKC") -> str:
    name = str(form).upper()
    if name not in ("NFC", "NFD", "NFKC", "NFKD"):
        raise StepError(f"normalize_unicode form must be NFC, NFD, NFKC or NFKD, got {form!r}")
    return unicodedata.normalize(name, _text_of(value))  # type: ignore[arg-type]


def _strip_prefix(value: Any, prefix: Any) -> str:
    text = _text_of(value)
    marker = str(prefix)
    # A cleanup, not a filter: absence means there was nothing to remove.
    return text[len(marker) :] if marker and text.startswith(marker) else text


def _strip_suffix(value: Any, suffix: Any) -> str:
    text = _text_of(value)
    marker = str(suffix)
    return text[: -len(marker)] if marker and text.endswith(marker) else text


def _replace(value: Any, pattern: Any = None, **kwargs: Any) -> str:
    if pattern is None:
        raise StepError("replace needs a `pattern`")
    return re.sub(str(pattern), str(kwargs.get("with", "")), _text_of(value))


def _regex(value: Any, pattern: Any = None, group: Any = 1) -> str:
    if pattern is None:
        raise StepError("regex needs a `pattern`")
    compiled = re.compile(str(pattern))
    match = compiled.search(_text_of(value))
    if match is None:
        return ""  # a filter: no match yields nothing
    if compiled.groups == 0:
        return match.group(0)
    index = int(group)
    if index > compiled.groups:
        raise StepError(f"regex has {compiled.groups} group(s), asked for {index}")
    return match.group(index) or ""


def _reject(value: Any, pattern: Any = None) -> str:
    if pattern is None:
        raise StepError("reject needs a `pattern`")
    text = _text_of(value)
    return "" if re.search(str(pattern), text) else text


# --------------------------------------------------------------------------------------- #
# list steps
# --------------------------------------------------------------------------------------- #


def _split(value: Any, separator: Any) -> List[str]:
    # Neither a filter nor a cleanup: with no separator present this yields one entry, so a
    # single author with no comma survives.
    return _text_of(value).split(str(separator))


def _drop_empty(values: Any) -> List[Any]:
    return [v for v in _as_list(values) if not _is_empty(v)]


def _unique(values: Any) -> List[Any]:
    seen = set()
    out = []
    for value in _as_list(values):
        key = value if isinstance(value, str) else repr(value)
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _join(values: Any, separator: Any) -> str:
    return str(separator).join(_text_of(v) for v in _as_list(values))


def _numbers_in(values: Any) -> List[str]:
    """Every entry that carries a number, as it was written.

    The original spelling is kept rather than the parsed value, because these read a page number off
    a pager and it goes straight back into a URL. Reformatting `007` as `7` would address a page the
    site does not have.
    """
    found = []
    for value in _as_list(values):
        match = re.search(r"-?\d+(?:\.\d+)?", _text_of(value))
        if match:
            found.append(match.group(0))
    return found


def _max(values: Any) -> str:
    numbers = _numbers_in(values)
    return max(numbers, key=float) if numbers else ""


def _min(values: Any) -> str:
    numbers = _numbers_in(values)
    return min(numbers, key=float) if numbers else ""


def _lines_to_html(values: Any, tag: Any = "p", attr: Any = None) -> str:
    name = str(tag)
    out = []
    for value in _as_list(values):
        text = _text_of(value)
        if not text.strip():
            continue
        if attr:
            out.append(f'<{name} {attr}="{text}"/>')
        else:
            out.append(f"<{name}>{text}</{name}>")
    return "".join(out)


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [] if value is None else [value]


# --------------------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------------------- #


def _entry(name: str, takes: Kind, gives: Kind, fn: Callable[..., Any]) -> Tuple[str, StepSpec]:
    return name, StepSpec(name, takes, gives, fn)


REGISTRY: Dict[str, StepSpec] = dict(
    [
        _entry("strip_tags", NODE, NODE, _strip_tags),
        _entry("strip_css", NODE, NODE, _strip_css),
        _entry("strip_matching", NODE, NODE, _strip_matching),
        _entry("unwrap", NODE, NODE, _unwrap),
        _entry("unwrap_all", NODE, NODE, _unwrap_all),
        _entry("keep_attrs", NODE, NODE, _keep_attrs),
        _entry("unlazy_images", NODE, NODE, _unlazy_images),
        _entry("drop_leading", NODE, NODE, _drop_leading),
        _entry("drop_empty_nodes", NODE, NODE, _drop_empty_nodes),
        _entry("paragraphs", NODE, HTML, _paragraphs),
        _entry("inner_html", NODE, HTML, _inner_html),
        _entry("parse_html", TEXT, NODE, _parse_html),
        _entry("text", NODE, TEXT, _text),
        _entry("trim", TEXT, TEXT, _trim),
        _entry("collapse_spaces", TEXT, TEXT, _collapse_spaces),
        _entry("lower", TEXT, TEXT, _lower),
        _entry("title_case", TEXT, TEXT, _title_case),
        _entry("normalize_unicode", TEXT, TEXT, _normalize_unicode),
        _entry("strip_prefix", TEXT, TEXT, _strip_prefix),
        _entry("strip_suffix", TEXT, TEXT, _strip_suffix),
        _entry("replace", TEXT, TEXT, _replace),
        _entry("regex", TEXT, TEXT, _regex),
        _entry("reject", TEXT, TEXT, _reject),
        _entry("split", TEXT, LIST, _split),
        _entry("drop_empty", LIST, LIST, _drop_empty),
        _entry("unique", LIST, LIST, _unique),
        _entry("join", LIST, TEXT, _join),
        _entry("max", LIST, TEXT, _max),
        _entry("min", LIST, TEXT, _min),
        _entry("lines_to_html", LIST, HTML, _lines_to_html),
    ]
)


def parse_step(step: Any) -> Tuple[str, Any]:
    """Split a declared step into its name and argument.

    A step is a bare name, a single-key mapping of name to argument, or `{hook: <path>}`.
    """
    if isinstance(step, str):
        return step, None
    if isinstance(step, dict):
        if len(step) != 1:
            raise StepError(f"a step must have exactly one key, got {sorted(step)}")
        name, argument = next(iter(step.items()))
        return str(name), argument
    raise StepError(f"a step must be a name or a single-key mapping, got {type(step).__name__}")


def _connects(produced: Kind, expected: Kind) -> bool:
    if produced == ANY or expected == ANY:
        return True
    if produced == expected:
        return True
    # HTML is a string, so anything taking text accepts it.
    if produced == HTML and expected == TEXT:
        return True
    # A scalar step maps element-wise over a list, so a list connects to a scalar input.
    if produced == LIST and expected in (TEXT, NODE):
        return True
    return False


def validate_pipe(steps: Sequence[Any], takes: Kind = ANY) -> Kind:
    """Check that *steps* connect, and return what the pipe produces.

    Raises StepError on an unknown step or a type mismatch, so a broken pipe is a load-time
    error rather than something discovered per chapter mid-crawl.
    """
    produced = takes
    for index, declared in enumerate(steps):
        name, _ = parse_step(declared)
        if name == "hook":
            produced = ANY
            continue
        spec = REGISTRY.get(name)
        if spec is None:
            raise StepError(f"step {index + 1} names an unknown step {name!r}")
        if not _connects(produced, spec.takes):
            raise StepError(
                f"step {index + 1} ({name}) consumes {spec.takes} but the pipe produced {produced}"
            )
        # A scalar step over a list yields a list of that step's output.
        produced = LIST if produced == LIST and spec.takes in (TEXT, NODE) else spec.gives
    return produced


def apply_step(value: Any, declared: Any) -> Any:
    """Apply one declared step to *value*, mapping element-wise over a list if needed."""
    name, argument = parse_step(declared)
    spec = REGISTRY.get(name)
    if spec is None:
        raise StepError(f"unknown step {name!r}")

    if isinstance(value, (list, tuple)) and spec.takes in (TEXT, NODE):
        return [apply_step(item, declared) for item in value]

    return _invoke(spec, value, argument)


def _invoke(spec: StepSpec, value: Any, argument: Any) -> Any:
    if argument is None:
        return spec.fn(value)
    if isinstance(argument, dict):
        return spec.fn(value, **{str(k): v for k, v in argument.items()})
    return spec.fn(value, argument)


def apply_pipe(value: Any, steps: Sequence[Any], pipes: Optional[Dict[str, Any]] = None) -> Any:
    """Apply *steps* in order. A step naming an entry in *pipes* expands into it."""
    current = value
    for declared in expand_pipe(steps, pipes or {}):
        current = apply_step(current, declared)
    return current


def expand_pipe(steps: Sequence[Any], pipes: Optional[Dict[str, Any]] = None) -> List[Any]:
    """Resolve every named-pipe reference in *steps* into the steps it stands for.

    Public because validation has to see the same expansion the run will, and it happens
    before any value exists. A name is only a reference when *pipes* holds it, so a step
    name is never shadowed by an absent entry.
    """
    return _expand(steps, pipes or {})


def _expand(
    steps: Sequence[Any], pipes: Dict[str, Any], seen: Union[FrozenSet[str], None] = None
) -> List[Any]:
    seen = seen or frozenset()
    out: List[Any] = []
    for declared in steps:
        if isinstance(declared, str) and declared in pipes:
            if declared in seen:
                raise StepError(f"named pipe {declared!r} refers to itself")
            out.extend(_expand(pipes[declared], pipes, seen | {declared}))
        else:
            out.append(declared)
    return out
