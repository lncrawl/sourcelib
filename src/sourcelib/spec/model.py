"""The source definition model, as defined by RFC-0001 section 3.

Two rules shape every validator here.

Field descriptions are load-bearing. They become the JSON Schema's ``description``, which
is what supplies editor hover text and autocompletion and what a model reads to write a
spec without being taught the format. RFC-0001 section 3 requires them here rather than in
comments for that reason.

And validation splits by whether inheritance could satisfy the rule. A constraint that is
*always* an error, such as two mutually exclusive keys both being set, belongs on the model
and runs against the raw document. A constraint that a parent could satisfy, such as a
stage needing an address, cannot run against a raw child that legitimately declares two
lines and inherits the rest; those live in the resolved-spec checks instead.
"""

from __future__ import annotations

import re
from typing import Any, Dict, FrozenSet, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Annotated, Literal

__all__ = [
    "ChapterStage",
    "Extractor",
    "ItemList",
    "NovelStage",
    "Paginate",
    "Request",
    "SearchStage",
    "SourceSpec",
    "TocStage",
    "Var",
    "SESSION_HOOK_POINTS",
    "STAGE_FIELDS",
    "hook_points",
]

Step = Union[str, Dict[str, Any]]
PipeRef = Union[str, List[Step]]
RepoPath = str
UrlTemplate = str
HookPoint = str

#: Every stage and the field names it defines. Hook points are derived from this rather
#: than listed separately, so the two can never disagree (RFC-0001 section 3.9.2).
STAGE_FIELDS: Dict[str, Tuple[str, ...]] = {
    "search": ("items",),
    "novel": ("title", "cover", "authors", "tags", "synopsis", "language"),
    "toc": ("items", "volumes"),
    "chapter": ("url", "body"),
}

#: Points that belong to the session rather than to a stage.
SESSION_HOOK_POINTS: Tuple[str, ...] = ("check_response", "login")


def hook_points() -> FrozenSet[str]:
    """Every legal hook point name."""
    points = set(SESSION_HOOK_POINTS)
    for stage, fields in STAGE_FIELDS.items():
        points.add(f"{stage}.request")
        points.update(f"{stage}.{field}" for field in fields)
    return frozenset(points)


class Node(BaseModel):
    """Shared configuration for every node in a spec.

    ``extra="forbid"`` is what rejects a key carrying a trailing underscore. Fields whose
    document key is a reserved word are declared under a private name with the key as an
    alias, and because ``populate_by_name`` stays off, the private name arrives as an
    unknown key and is refused. RFC-0001 section 3.9.1 requires exactly that.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=False)


class Extractor(Node):
    """Reads one value from one document."""

    css: Optional[str] = Field(
        default=None,
        description="A CSS selector, evaluated relative to the node in scope. Supports "
        "Selectors Level 3 plus :-soup-contains(), :has() and :scope.",
    )
    json_: Optional[str] = Field(
        default=None,
        alias="json",
        description="A dotted path into a JSON body, or into the element css selected. "
        "'$' denotes the whole body; segments may be keys or numeric indices.",
    )
    regex: Optional[str] = Field(
        default=None,
        description="A regular expression applied to the document's raw text, or to the "
        "element css selected.",
    )
    header: Optional[str] = Field(
        default=None,
        description="A response header, by name, from the response this stage's request "
        "produced. Cannot be combined with any other source.",
    )
    const: Optional[Any] = Field(
        default=None,
        description="A literal. Interpolation still applies, so this produces a value "
        "from vars alone when the page does not carry it.",
    )

    attr: Union[str, List[str]] = Field(
        default="text",
        description="'text', 'html' (inner markup), 'outer_html', or attribute names. A "
        "list is tried in order and may mix them.",
    )
    all: bool = Field(
        default=False,
        description="Produce a list of every match instead of the first.",
    )
    pipe: Optional[PipeRef] = Field(
        default=None,
        description="Transforms to apply: a name from 'pipes', an inline list of steps, "
        "or a mix. Absent means the field's default pipe.",
    )
    fallback: List["Extractor"] = Field(
        default_factory=list,
        description="Whole alternative Extractors, tried in order while the result is "
        "empty. Add a new selector ahead of the old one rather than replacing it.",
    )
    default: Optional[Any] = Field(
        default=None,
        description="Used when everything else produced nothing.",
    )

    @model_validator(mode="after")
    def _check_sources(self) -> "Extractor":
        if self.const is not None and self._other_sources("const"):
            raise ValueError("const cannot be combined with another source")
        if self.header is not None and self._other_sources("header"):
            raise ValueError("header cannot be combined with another source")
        if self.json_ is not None and self.regex is not None:
            raise ValueError("json and regex cannot both read the same element")
        return self

    def _other_sources(self, besides: str) -> bool:
        present = {
            name
            for name in ("css", "json_", "regex", "header", "const")
            if getattr(self, name) is not None
        }
        return bool(present - {besides, f"{besides}_"})


class Paginate(Node):
    """How a stage walks more than one page.

    `first` and `last` are the numbers the *site* puts on its own pages, not a count of them. The
    stage's own request already produced `first`, so the pages fetched through `url` are the ones
    after it, up to and including `last`.
    """

    while_: Optional[Literal["has_items"]] = Field(
        default=None,
        alias="while",
        description="Stop at the first page yielding nothing. Use when the last page is unknown.",
    )
    first: Union[int, Extractor] = Field(
        default=1,
        description="The number this site gives its first page, which the stage's own request "
        "already fetched. Defaults to 1. Set 0 for a site numbering from zero.",
    )
    last: Optional[Union[int, Extractor]] = Field(
        default=None,
        description="The number of the last page, written literally or read from the first page. "
        "Where several numbers match, the largest wins.",
    )
    next: Optional[Extractor] = Field(
        default=None,
        description="Reads a link to the next page from the current document.",
    )
    url: Optional[UrlTemplate] = Field(
        default=None,
        description="Produces the pages after the first. Required with 'while' and 'last', "
        "invalid with 'next', which takes its address from the link.",
    )
    concurrent: Optional[bool] = Field(
        default=None,
        description="Fetch pages in parallel. Left unset it is on wherever the termination "
        "condition allows it, which is 'last' and 'while'. Never with 'next', whose pages are "
        "only known one at a time. Set false to force one request at a time.",
    )

    @model_validator(mode="after")
    def _check_termination(self) -> "Paginate":
        chosen = [
            name
            for name, value in (
                ("while", self.while_),
                ("last", self.last),
                ("next", self.next),
            )
            if value is not None
        ]
        if len(chosen) > 1:
            raise ValueError(f"only one of while, last, next may be set, got {chosen}")
        if self.next is not None and self.url is not None:
            raise ValueError("url is invalid with next, which takes its URL from the link")
        if self.concurrent and self.next is not None:
            # Only an *explicit* true is an error, and it is worth reporting because the author has
            # misunderstood the mechanism: a next link is known only once the page holding it has
            # been read. Left unset it simply does not apply, which is why the default is not a
            # plain false.
            raise ValueError("concurrent is invalid with next, whose pages are only known in turn")
        return self

    @property
    def runs_concurrently(self) -> bool:
        """Whether pages may be fetched in parallel, with the default resolved.

        Parallel by default because reading the chapter list is what a crawl spends its time on, and
        a list addressed by page number has no reason to be walked one round trip at a time. The
        host's pace is unaffected: it applies per origin, so this decides how many requests may wait
        on that budget rather than how large it is.
        """
        if self.next is not None:
            return False
        return True if self.concurrent is None else self.concurrent


class Request(Node):
    """Everything about making a request. Reading the response belongs to the stage."""

    name: Optional[str] = Field(
        default=None,
        description="Names this request so 'page' can reference it. Every stage's own "
        "request is already named after its stage.",
    )
    get: Optional[UrlTemplate] = Field(default=None, description="A GET URL template.")
    post: Optional[UrlTemplate] = Field(default=None, description="A POST URL template.")
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="The request body, valid only with post. Sent as JSON when it holds "
        "a nested object, list, boolean or number, and form-encoded otherwise. An "
        "explicit content-type header overrides that.",
    )
    form: Optional[str] = Field(
        default=None,
        description="A selector for a form. Its inputs are harvested into payload by "
        "name, then payload is applied over the result. With post, the form comes from "
        "the document in scope; with get, from the document get fetches, and the request "
        "goes to the form's own action.",
    )
    headers: Dict[str, str] = Field(
        default_factory=dict,
        description="Headers for this request, merged over the spec's own.",
    )
    encoding: Optional[str] = Field(
        default=None,
        description="Overrides the spec encoding for this request. Governs the request "
        "body as well as the response.",
    )
    render: bool = Field(
        default=False,
        description="Run the page's scripts before parsing, for a site whose content is "
        "not in the served markup. Far slower than an HTTP fetch.",
    )
    wait_for: Optional[str] = Field(
        default=None,
        description="A selector to wait for before reading. Should be set whenever render is.",
    )
    page: Optional[str] = Field(
        default=None,
        description="Reuse a document already fetched in this operation, by stage name "
        "or declared request name.",
    )
    from_: List["Request"] = Field(
        default_factory=list,
        alias="from",
        description="Alternatives tried in order until one yields items.",
    )
    paginate: Optional[Paginate] = Field(
        default=None,
        description="How this stage walks more than one page.",
    )

    @model_validator(mode="after")
    def _check_shape(self) -> "Request":
        addresses = [
            name
            for name, value in (("get", self.get), ("post", self.post), ("page", self.page))
            if value is not None
        ]
        if self.from_:
            addresses.append("from")
        if len(addresses) > 1:
            raise ValueError(f"only one of get, post, page, from may be set, got {addresses}")
        # With get + form the request posts to the form's own action, so it carries a
        # payload without naming a post URL (RFC-0001 section 3.6).
        if self.payload and self.post is None and not (self.get and self.form):
            raise ValueError("payload is valid only with post, or with get and form together")
        if self.wait_for is not None and not self.render:
            raise ValueError("wait_for is meaningless without render")
        return self

    @property
    def has_address(self) -> bool:
        """Whether this request names where to go, rather than inheriting a stage default."""
        return bool(self.get or self.post or self.page or self.from_)


class Var(Extractor):
    """A named Extractor whose result templates read as ``{vars.<name>}``."""

    on: Union[Literal["url", "novel", "chapter"], Request] = Field(
        default="novel",
        description="What this var reads: the novel URL string, the novel page, the "
        "chapter page, or its own request (cached for the session).",
    )
    renew: bool = Field(
        default=False,
        description="Re-evaluate and retry once when a request using this var is "
        "refused. For credentials that expire mid-crawl.",
    )

    @model_validator(mode="after")
    def _check_own_request(self) -> "Var":
        # A session-scoped var outlives every per-novel and per-chapter placeholder, and
        # may be evaluated while the document that would satisfy `page` is still being
        # read (RFC-0001 section 4.2).
        if isinstance(self.on, Request):
            if self.on.page is not None:
                raise ValueError("a var's own request cannot use page")
            forbidden = ("{query}", "{novel_url}", "{chapter.", "{item.", "{page}")
            for text in _templates_of(self.on):
                for placeholder in forbidden:
                    if placeholder in text:
                        raise ValueError(
                            f"a var's own request cannot use {placeholder.rstrip('.')}"
                        )
        return self


def _templates_of(request: Request) -> List[str]:
    texts: List[str] = [t for t in (request.get, request.post) if t]
    texts.extend(str(v) for v in request.payload.values())
    texts.extend(request.headers.values())
    return texts


class ItemList(Node):
    """A repeated structure: a container, and per-row Extractors evaluated inside it."""

    request: Optional[Request] = Field(
        default=None,
        description="The request producing the document these rows come from.",
    )
    css: Optional[str] = Field(default=None, description="Selects the row container.")
    json_: Optional[str] = Field(
        default=None,
        alias="json",
        description="A dotted path to an array of rows.",
    )
    sort_by: Optional[str] = Field(
        default=None,
        description="Orders rows by a field, compared numerically. A row whose value is "
        "not a number sorts after every row that is.",
    )
    reverse: bool = Field(
        default=False,
        description="Reverse the order, applied after sort_by.",
    )
    fields: Dict[str, Union[Extractor, UrlTemplate]] = Field(
        default_factory=dict,
        description="Per-row Extractors, each evaluated with that row in scope. Extra "
        "keys are preserved on the item and readable later as {chapter.<key>}. A field "
        "may instead be a URL template referencing earlier siblings as {item.<name>}.",
    )


class SearchStage(ItemList):
    """Search results. Rows carry title, url and info."""


class NovelStage(Node):
    """The novel's own metadata."""

    request: Optional[Request] = Field(
        default=None,
        description="Defaults to a GET of the novel URL.",
    )
    title: Optional[Extractor] = Field(default=None, description="The novel title.")
    cover: Optional[Extractor] = Field(default=None, description="The cover image URL.")
    authors: Optional[Extractor] = Field(default=None, description="The author names.")
    tags: Optional[Extractor] = Field(default=None, description="Genre or tag names.")
    synopsis: Optional[Extractor] = Field(default=None, description="The description.")
    language: Optional[Extractor] = Field(
        default=None,
        description="A per-novel language, for hosts serving more than one.",
    )


class TocStage(Node):
    """The table of contents."""

    request: Optional[Request] = Field(
        default=None,
        description="The request producing the chapter list.",
    )
    items: Optional[ItemList] = Field(
        default=None,
        description="The chapter rows. The only source of chapters.",
    )
    volumes: Optional[ItemList] = Field(
        default=None,
        description="Volume heading rows interleaved with the chapter rows, over the "
        "same container. Each chapter takes the nearest preceding heading.",
    )


class ChapterStage(Node):
    """One chapter's body."""

    request: Optional[Request] = Field(
        default=None,
        description="Defaults to a GET of the URL the table of contents captured. Carries "
        "paginate when a body spans several pages.",
    )
    url: Optional[Union[Extractor, UrlTemplate]] = Field(
        default=None,
        description="Defaults to the URL captured by the table of contents.",
    )
    body: Optional[Extractor] = Field(default=None, description="The chapter text.")
    join: str = Field(
        default="",
        description="Joins the bodies of a multi-page chapter, in order.",
    )


_HOST_RE = re.compile(r"^https?://", re.IGNORECASE)


class SourceSpec(Node):
    """One source definition: how to read one website."""

    spec: int = Field(
        description="The version of the whole contract: this model, the step registry "
        "and the hook points. An interpreter refuses a version it does not implement.",
        ge=1,
    )
    extends: Optional[RepoPath] = Field(
        default=None,
        description="A repository-relative path to one parent spec, in specs/ or base/.",
    )
    base_url: Optional[str] = Field(
        default=None,
        description="The site's absolute root URL. Its host must match the filename. "
        "Absent makes the spec abstract.",
    )
    language: Optional[str] = Field(
        default=None,
        description="An ISO 639-1 two-letter code, used only as a starting default. "
        "Detection from the fetched content wins.",
    )
    rate_limit: float = Field(
        default=3.0,
        gt=0,
        description="Requests per second for this host.",
    )
    chapters_per_volume: int = Field(
        default=100,
        gt=0,
        description="How many chapters make a volume when the site declares none.",
    )
    has_manga: bool = Field(default=False, description="Chapters are images, not text.")
    has_mtl: bool = Field(default=False, description="Content is machine-translated.")
    parser: Optional[str] = Field(
        default=None,
        description=(
            "The markup parser. Defaults to lxml; set html.parser for a page lxml restructures."
        ),
    )
    encoding: Optional[str] = Field(
        default=None,
        description="A character encoding, for a site that does not declare its own.",
    )
    headers: Dict[str, str] = Field(
        default_factory=dict,
        description="Headers for every request. A spec cannot set User-Agent or control "
        "header order; those belong to the HTTP layer.",
    )
    can_search: Optional[bool] = Field(
        default=None,
        description="Derived when absent. false forces an inherited search off; true is "
        "rejected unless a search stage resolves.",
    )
    can_login: Optional[bool] = Field(
        default=None,
        description="Derived when absent. false forces an inherited login off; true is "
        "rejected unless a login hook resolves.",
    )
    disabled: Optional[str] = Field(
        default=None,
        description="Why this host is not served. Present if and only if the document "
        "lives in disabled/.",
    )

    vars: Dict[str, Var] = Field(
        default_factory=dict,
        description="Named Extractors, read by templates as {vars.<name>}.",
    )
    pipes: Dict[str, List[Step]] = Field(
        default_factory=dict,
        description="Reusable transform pipes by name, inheritable through extends.",
    )
    hooks: Union[RepoPath, Dict[HookPoint, RepoPath]] = Field(
        default_factory=dict,
        description="A hook file binding every point it defines, or a mapping of point "
        "to file. Points are '<stage>.<field>', '<stage>.request', 'check_response' or "
        "'login'.",
    )

    search: Optional[SearchStage] = Field(
        default=None,
        description="How to search the site. Absent means the source cannot search.",
    )
    novel: Optional[NovelStage] = Field(
        default=None,
        description="How to read a novel's metadata.",
    )
    toc: Optional[TocStage] = Field(
        default=None,
        description="How to read the chapter list.",
    )
    chapter: Optional[ChapterStage] = Field(
        default=None,
        description="How to read one chapter's text.",
    )

    @field_validator("base_url")
    @classmethod
    def _absolute_url(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _HOST_RE.match(value):
            raise ValueError("base_url must be an absolute http or https URL")
        return value

    @field_validator("language")
    @classmethod
    def _iso639(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not re.fullmatch(r"[a-z]{2}", value):
            raise ValueError("language must be an ISO 639-1 two-letter code")
        return value

    @field_validator("hooks")
    @classmethod
    def _known_points(cls, value: Union[str, Dict[str, str]]) -> Union[str, Dict[str, str]]:
        if isinstance(value, dict):
            legal = hook_points()
            for point in value:
                if point not in legal:
                    raise ValueError(
                        f"unknown hook point {point!r}; expected one of {sorted(legal)}"
                    )
        return value


Extractor.model_rebuild()
Request.model_rebuild()

#: The document root, annotated so the generated schema carries a title.
Spec = Annotated[SourceSpec, Field(description="A light-novel source definition.")]
