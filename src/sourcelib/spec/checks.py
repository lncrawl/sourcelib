"""Requirements that apply to a resolved spec, per RFC-0001 sections 3.2, 3.3 and 3.6.

These are deliberately not model validators. A raw child document may declare two lines and
inherit everything else, so any rule inheritance can satisfy has to wait until the document
is merged. Running them earlier would reject the alias spec that ``extends`` exists for.
"""

from __future__ import annotations

from typing import AbstractSet, Any, Dict, Iterator, List, Optional, Tuple

from pydantic import BaseModel

from sourcelib.spec.model import SourceSpec
from sourcelib.transform import ANY, LIST, StepError, expand_pipe, validate_pipe

__all__ = ["Problem", "check_resolved", "derived_capabilities"]


class Problem:
    """One requirement a resolved spec fails, named where an author can act on it."""

    __slots__ = ("field", "message")

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Problem({self.field!r}, {self.message!r})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Problem)
            and self.field == other.field
            and self.message == other.message
        )


def _hook_points(spec: SourceSpec) -> Optional[Dict[str, str]]:
    """The declared point-to-file mapping, or None when the spec names a whole file.

    A bare path binds every point the file happens to define, and learning which requires
    importing it. That belongs to the hook loader, so here it means "cannot be disproved".
    """
    return spec.hooks if isinstance(spec.hooks, dict) else None


def _hooked(spec: SourceSpec, *points: str) -> bool:
    declared = _hook_points(spec)
    if declared is None:
        return True
    return any(point in declared for point in points)


def _hooked_prefix(spec: SourceSpec, prefix: str) -> bool:
    declared = _hook_points(spec)
    if declared is None:
        return True
    return any(point.startswith(prefix) for point in declared)


def check_resolved(spec: SourceSpec) -> List[Problem]:
    """Every requirement *spec* fails. An empty list means it is servable."""
    problems: List[Problem] = []

    # A disabled host is an answer rather than an implementation, so it is exempt. An
    # abstract spec exists to be extended and is never served on its own.
    concrete = spec.base_url is not None and spec.disabled is None

    if concrete:
        problems.extend(_check_capable(spec))
    problems.extend(_check_addresses(spec))
    problems.extend(_check_claims(spec))
    problems.extend(_check_page_order(spec))
    problems.extend(_check_pipes(spec))
    problems.extend(_check_require(spec))
    return problems


def _item_lists(spec: SourceSpec) -> Iterator[Tuple[str, Any]]:
    for name in ("search", "toc"):
        stage = getattr(spec, name, None)
        if stage is None:
            continue
        if name == "search":
            yield name, stage
            continue
        for part in ("items", "volumes"):
            value = getattr(stage, part, None)
            if value is not None:
                yield f"{name}.{part}", value


def _check_require(spec: SourceSpec) -> List[Problem]:
    """A name in `require` must be a field the list declares.

    A misspelling would otherwise read as a field that resolves empty on every row, which drops
    the whole list. Reporting zero chapters is the same thing a dead selector does, so nothing
    would point at the typo.
    """
    problems: List[Problem] = []
    for where, item_list in _item_lists(spec):
        declared = set(getattr(item_list, "fields", None) or ())
        for name in getattr(item_list, "require", None) or ():
            if name not in declared:
                problems.append(
                    Problem(
                        f"{where}.require",
                        f"{name!r} is not a declared field, so every row would be dropped",
                    )
                )
    return problems


def _pipes_in(node: Any, path: str) -> Iterator[Tuple[str, Any, str]]:
    """Every declared pipe in *node*, with the path to it and what it is handed.

    The input kind matters: `all: true` hands the pipe a list, and a scalar step then maps over it
    and yields a list again. Assuming a scalar start instead rejects `[trim, drop_empty, join]`,
    which is what half the live bases do with a multi-valued field.
    """
    if isinstance(node, BaseModel):
        fields = type(node).model_fields
        for name in fields:
            value = getattr(node, name, None)
            if value is None:
                continue
            shown = fields[name].alias or name
            where = f"{path}.{shown}" if path else shown
            if name == "pipe":
                yield where, value, LIST if getattr(node, "all", False) else ANY
            else:
                yield from _pipes_in(value, where)
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _pipes_in(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _pipes_in(value, f"{path}[{index}]")


def _check_pipes(spec: SourceSpec) -> List[Problem]:
    """Section 6: every step a pipe names must exist, and consecutive steps must connect.

    The registry already knows what each step consumes and produces, and `validate_pipe` has
    always been able to say so. Nothing called it on a spec, so a misspelled step passed
    validation and failed once per chapter mid-crawl, naming the step but not the file.

    The input kind comes from `all`, which is the one thing about it a spec states plainly. What
    an extractor hands a scalar pipe depends on `attr` and on section 3.4's node-preserving rule,
    so that case stays unasserted and the checking effectively begins at the second step.
    """
    problems: List[Problem] = []
    declared = spec.pipes or {}

    for name, steps in declared.items():
        # A named pipe is checked from an unknown input, since any field may reference it.
        problems.extend(_check_one_pipe(f"pipes.{name}", steps, declared, ANY))
    for where, steps, takes in _pipes_in(spec, ""):
        problems.extend(_check_one_pipe(where, steps, declared, takes))
    return problems


def _check_one_pipe(
    where: str, steps: Any, declared: Dict[str, Any], takes: str = ANY
) -> List[Problem]:
    if isinstance(steps, str):
        if steps not in declared:
            return [Problem(where, f"names no pipe in 'pipes': {steps!r}")]
        steps = [steps]
    try:
        validate_pipe(expand_pipe(steps, declared), takes)
    except StepError as error:
        return [Problem(where, str(error))]
    return []


#: The order stages run in (section 3.6), which is what makes a `page` reference resolvable.
_STAGE_ORDER = ("search", "novel", "toc", "chapter")


def _requests_of(stage: object):
    """Every request a stage carries: its own, and each `from` alternative."""
    request = getattr(stage, "request", None)
    if request is None:
        return
    yield request
    for alternative in getattr(request, "from_", None) or ():
        yield alternative


def _check_page_order(spec: SourceSpec) -> List[Problem]:
    """Section 3.6: a `page` MUST name a request that has already run.

    Enforced here rather than left to the fetcher. A stage reusing its own document is the
    natural typo (`novel: request: {page: novel}` reads as "the novel page") and it used to
    surface as a mid-crawl error naming the cache, which says nothing about the spec.
    """
    problems: List[Problem] = []
    names: set = set()

    for position, stage_name in enumerate(_STAGE_ORDER):
        stage = getattr(spec, stage_name, None)
        if stage is None:
            continue
        earlier = set(_STAGE_ORDER[:position]) | names
        for request in _requests_of(stage):
            page = getattr(request, "page", None)
            if page is not None and page not in earlier:
                reason = (
                    "a stage cannot reuse its own document"
                    if page == stage_name
                    else f"{page} has not run by then; stages run {', '.join(_STAGE_ORDER)}"
                )
                problems.append(Problem(f"{stage_name}.request.page", reason))
        # A name becomes referenceable only once its own stage has run.
        names.update(
            request.name for request in _requests_of(stage) if getattr(request, "name", None)
        )

    return problems


def _check_capable(spec: SourceSpec) -> List[Problem]:
    """RFC-0001 section 3.3: a served spec must be able to produce all three things."""
    problems: List[Problem] = []

    if spec.novel is None and not _hooked_prefix(spec, "novel."):
        problems.append(
            Problem("novel", "a served spec must declare a novel stage or hook one of its points")
        )

    if spec.toc is None or spec.toc.items is None:
        if not _hooked(spec, "toc.items"):
            problems.append(
                Problem("toc.items", "chapters must come from toc.items or a toc.items hook")
            )

    if spec.chapter is None or spec.chapter.body is None:
        if not _hooked(spec, "chapter.body"):
            problems.append(
                Problem(
                    "chapter.body",
                    "a chapter body must come from chapter.body or a chapter.body hook",
                )
            )

    return problems


def _check_addresses(spec: SourceSpec) -> List[Problem]:
    """RFC-0001 section 3.6: search and toc have no default address.

    novel and chapter do, so a request of theirs may carry only pagination.
    """
    problems: List[Problem] = []
    for name, request, hooked in (
        ("search", spec.search.request if spec.search else None, _hooked(spec, "search.request")),
        ("toc", spec.toc.request if spec.toc else None, _hooked(spec, "toc.request")),
    ):
        stage = getattr(spec, name)
        if stage is None or hooked:
            continue
        if request is None:
            problems.append(
                Problem(f"{name}.request", f"{name} has no default address, so it must declare one")
            )
        elif not request.has_address:
            problems.append(
                Problem(
                    f"{name}.request",
                    "must set one of get, post, page or from; only novel and chapter "
                    "inherit an address from their stage",
                )
            )
    return problems


def _check_claims(spec: SourceSpec) -> List[Problem]:
    """RFC-0001 section 3.2: a capability may be forced off, never claimed falsely."""
    problems: List[Problem] = []

    if spec.can_search is True and spec.search is None and not _hooked_prefix(spec, "search."):
        problems.append(Problem("can_search", "is true but no search stage or hook resolves"))

    if spec.can_login is True and not _hooked(spec, "login"):
        problems.append(Problem("can_login", "is true but no login hook resolves"))

    return problems


def derived_capabilities(spec: SourceSpec, bound: AbstractSet[str]) -> Dict[str, bool]:
    """What a resolved spec can do, with an explicit false winning over derivation.

    *bound* is the set of hook points actually bound, which the hook loader knows and this
    module cannot: a spec naming a whole file binds whatever that file defines. Deriving
    from the declaration alone would report `can_login` for every source carrying any hook.
    """
    can_search = spec.search is not None or any(p.startswith("search.") for p in bound)
    can_login = "login" in bound
    return {
        "can_search": can_search if spec.can_search is None else spec.can_search,
        "can_login": can_login if spec.can_login is None else spec.can_login,
    }
