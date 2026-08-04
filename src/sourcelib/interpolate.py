"""Template interpolation, per RFC-0001 section 4.2.

Both sets are closed: there are no expressions, no arithmetic and no conditionals, and an
unknown placeholder or filter is a load-time error rather than an empty string at crawl time.
That closedness is what keeps the format from drifting into a bad programming language, so
validation is a separate pass from rendering and CI runs it without fetching anything.

Availability is scoped. `{query}` means nothing outside a search and `{chapter.*}` means
nothing before a chapter exists, so each is legal only where it can be resolved.
"""

from __future__ import annotations

import re
from typing import Any, Collection, Dict, FrozenSet, List, Mapping, Optional, Tuple
from urllib.parse import quote, quote_plus

__all__ = [
    "FILTERS",
    "PLACEHOLDER_ROOTS",
    "TemplateError",
    "allowed_roots",
    "apply_filter",
    "placeholders_in",
    "render",
    "validate_template",
]

#: `{name}` or `{name|filter|filter}`. Deliberately not a general expression grammar.
_TOKEN = re.compile(r"\{([^{}]*)\}")

#: Every placeholder root, and where it can be resolved.
PLACEHOLDER_ROOTS: Dict[str, str] = {
    "origin": "everywhere",
    "vars": "everywhere",
    "query": "the search stage",
    "novel_url": "the novel, toc and chapter stages",
    "request_url": "a paginate url",
    "page": "a paginate url",
    "chapter": "the chapter stage",
    "item": "a field inside an ItemList",
    "username": "the login hook",
    "password": "the login hook",
}

#: Roots legal in every scope.
_ALWAYS: FrozenSet[str] = frozenset({"origin", "vars"})

#: Roots each stage adds on top of `_ALWAYS`.
_BY_STAGE: Dict[str, FrozenSet[str]] = {
    "search": frozenset({"query"}),
    "novel": frozenset({"novel_url"}),
    "toc": frozenset({"novel_url"}),
    "chapter": frozenset({"novel_url", "chapter"}),
    "login": frozenset({"username", "password"}),
    # A var's own request is session-scoped, so it outlives every other placeholder.
    "var": frozenset(),
}


class TemplateError(Exception):
    """A template names a placeholder or filter that does not exist, or one out of scope."""


def allowed_roots(
    stage: str,
    in_paginate: bool = False,
    in_item: bool = False,
) -> FrozenSet[str]:
    """Which placeholder roots may appear in a template at this position."""
    if stage not in _BY_STAGE:
        raise TemplateError(f"unknown stage {stage!r}; expected one of {sorted(_BY_STAGE)}")
    roots = set(_ALWAYS) | set(_BY_STAGE[stage])
    if in_paginate:
        # Later pages are built from the address already fetched, which may have come from a
        # var or a redirect rather than from the spec.
        roots |= {"request_url", "page"}
    if in_item:
        roots |= {"item"}
    return frozenset(roots)


def _slug(text: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return lowered.strip("-")


#: The closed filter set. Three URL encodings rather than one because sites genuinely differ
#: and collapsing them sends the wrong query.
FILTERS: Dict[str, Any] = {
    "plus": lambda text: text.replace(" ", "+"),
    "urlencode": lambda text: quote(text, safe=""),
    "urlencode_plus": quote_plus,
    "lower": lambda text: text.lower(),
    "slug": _slug,
}


def apply_filter(name: str, text: str) -> str:
    """Apply one named filter."""
    handler = FILTERS.get(name)
    if handler is None:
        raise TemplateError(f"unknown filter {name!r}; expected one of {sorted(FILTERS)}")
    return handler(text)


def placeholders_in(template: str) -> List[Tuple[str, List[str]]]:
    """Every placeholder in *template*, as (path, filters)."""
    found: List[Tuple[str, List[str]]] = []
    for token in _TOKEN.findall(template):
        parts = [p.strip() for p in token.split("|")]
        found.append((parts[0], [p for p in parts[1:] if p]))
    return found


def validate_template(template: str, roots: Collection[str]) -> None:
    """Reject a template naming an unknown placeholder, an unknown filter, or one out of scope.

    *roots* is what `allowed_roots` returned for this position.
    """
    permitted = set(roots)
    for path, filters in placeholders_in(template):
        if not path:
            raise TemplateError("an empty placeholder {} is not valid")
        root = path.split(".", 1)[0]
        if root not in PLACEHOLDER_ROOTS:
            raise TemplateError(
                f"unknown placeholder {{{path}}}; expected one of {sorted(PLACEHOLDER_ROOTS)}"
            )
        if root not in permitted:
            raise TemplateError(
                f"{{{path}}} is not available here; it belongs to {PLACEHOLDER_ROOTS[root]}"
            )
        if root in ("vars", "chapter", "item") and "." not in path:
            raise TemplateError(f"{{{path}}} needs a name, as in {{{root}.something}}")
        for name in filters:
            if name not in FILTERS:
                raise TemplateError(f"unknown filter {name!r}; expected one of {sorted(FILTERS)}")


def _lookup(path: str, context: Mapping[str, Any]) -> Any:
    current: Any = context
    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return None
            current = current[part]
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def render(template: str, context: Mapping[str, Any], strict: bool = True) -> str:
    """Substitute every placeholder in *template* from *context*.

    With *strict*, a placeholder the context cannot resolve is an error. That is the default
    because a URL silently missing an identifier produces a request to the wrong page, which
    reads as the site having changed.
    """

    def substitute(match: "re.Match[str]") -> str:
        parts = [p.strip() for p in match.group(1).split("|")]
        path, filters = parts[0], [p for p in parts[1:] if p]
        value = _lookup(path, context)
        if value is None:
            if strict:
                raise TemplateError(f"{{{path}}} has no value in this context")
            return ""
        text = str(value)
        for name in filters:
            text = apply_filter(name, text)
        return text

    return _TOKEN.sub(substitute, template)


def context_for(
    origin: str,
    variables: Optional[Mapping[str, Any]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """Build a render context with the roots this position supplies."""
    context: Dict[str, Any] = {"origin": origin, "vars": dict(variables or {})}
    context.update({k: v for k, v in extra.items() if v is not None})
    return context
