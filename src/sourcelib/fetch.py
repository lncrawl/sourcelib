"""Making requests and walking pages, per RFC-0001 sections 3.6 and 3.7.

The HTTP layer sits behind a small protocol rather than being imported directly. That keeps
the fetch engine testable with no network, and it keeps ``lncrawl-scraper`` an optional extra
so validating a spec does not need a TLS impersonation stack.

Two rules here are easy to get wrong and hard to notice afterwards. Pagination assembles by
page index rather than by completion order, because chapter numbering is what the rest of the
system stores and compares. And ``page:`` reuses an already-fetched document rather than
fetching a second time, so a stage cannot quietly double a site's traffic.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple
from urllib.parse import urljoin

from sourcelib.interpolate import render, render_url
from sourcelib.spec.extract import DEFAULT_PARSER, Document, extract
from sourcelib.spec.model import Paginate, Request

__all__ = [
    "DEFAULT_WORKERS",
    "SPECULATIVE_WORKERS",
    "FetchError",
    "Fetched",
    "Fetcher",
    "RecordedFetcher",
    "encode_payload",
    "harvest_form",
    "run_request",
    "walk_pages",
]

#: How many pages may be in flight when `concurrent` is set. The host's rate limit still applies
#: underneath, and it should be the thing that limits a walk rather than this number: a site
#: answering in five seconds under a three-per-second budget needs roughly fifteen requests waiting
#: to keep that budget saturated, so a small pool silently paces slower than the spec asked for.
DEFAULT_WORKERS = 16

#: How many pages a `while` walk may request at once. Smaller than the pool on purpose: that walk is
#: speculative, so a window is also the number of requests it can waste past the end of the list.
#: `count` knows where to stop and has no such cost.
SPECULATIVE_WORKERS = 4


class FetchError(Exception):
    """A request could not be made, or `page:` named a document not yet fetched."""


class Fetched:
    """One response, reduced to what a spec can read."""

    __slots__ = ("url", "text", "headers", "status")

    def __init__(
        self,
        url: str,
        text: str,
        headers: Optional[Mapping[str, str]] = None,
        status: int = 200,
    ) -> None:
        self.url = url
        self.text = text
        self.headers = dict(headers or {})
        self.status = status


class Fetcher(Protocol):  # pragma: no cover - a structural type
    """What the fetch engine needs from an HTTP layer."""

    def fetch(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        form: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        encoding: Optional[str] = None,
    ) -> Fetched: ...

    def render(self, url: str, *, wait_for: Optional[str] = None) -> Fetched: ...


class RecordedFetcher:
    """A Fetcher answering from a mapping, for tests and for replaying fixtures."""

    def __init__(self, pages: Mapping[str, Any]) -> None:
        self.pages = dict(pages)
        self.calls: List[Tuple[str, str]] = []
        self.bodies: List[Dict[str, Any]] = []

    def fetch(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        form: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        encoding: Optional[str] = None,
    ) -> Fetched:
        self.calls.append((method, url))
        self.bodies.append(
            {"form": form, "json": json, "encoding": encoding, "headers": dict(headers or {})}
        )
        if url not in self.pages:
            raise FetchError(f"nothing recorded for {url}")
        recorded = self.pages[url]
        if isinstance(recorded, Fetched):
            return recorded
        if isinstance(recorded, tuple):
            body, response_headers = recorded
            return Fetched(url, body, response_headers)
        return Fetched(url, str(recorded))

    def render(self, url: str, *, wait_for: Optional[str] = None) -> Fetched:
        self.calls.append(("RENDER", url))
        return self.fetch("GET", url)


def encode_payload(
    payload: Mapping[str, Any], headers: Mapping[str, str]
) -> Tuple[str, Mapping[str, Any]]:
    """Decide how a body goes on the wire, as ("json" | "form", body).

    Inference rather than a declaration because there is nothing to forget: a payload holding a
    nested object, a list, a boolean or a number cannot be form-encoded. An explicit
    content-type header overrides it, which is how an API whose body happens to be flat strings
    says so.
    """
    declared = next((v for k, v in headers.items() if k.lower() == "content-type"), None)
    if declared:
        return ("json" if "json" in declared.lower() else "form", payload)
    if any(isinstance(v, (dict, list, tuple, bool, int, float)) for v in payload.values()):
        return "json", payload
    return "form", payload


def harvest_form(document: Document, selector: str) -> Tuple[Dict[str, str], Optional[str]]:
    """Read a form's inputs and its action.

    A body carrying hidden inputs, a verification token or a per-visit state blob cannot have
    those values hardcoded, so they are read off the form.
    """
    if document.node is None:
        raise FetchError("harvesting a form needs a parsed document")
    form = document.node.select_one(selector)
    if form is None:
        raise FetchError(f"form selector {selector!r} matched nothing")

    values: Dict[str, str] = {}
    for field in form.select("input[name], select[name], textarea[name]"):
        name = field.get("name")
        if name:
            values[str(name)] = str(field.get("value") or "")

    action = form.get("action")
    resolved = urljoin(document.url, str(action)) if action else (document.url or None)
    return values, resolved


def run_request(
    request: Request,
    fetcher: Fetcher,
    context: Mapping[str, Any],
    cache: Optional[Dict[str, Document]] = None,
    default_url: Optional[str] = None,
    parser: str = DEFAULT_PARSER,
    spec_headers: Optional[Mapping[str, str]] = None,
    spec_encoding: Optional[str] = None,
    yields: Optional[Callable[[Document], bool]] = None,
) -> Document:
    """Perform *request* and return the document it produced.

    *default_url* is the stage's own address, which `novel` and `chapter` inherit when their
    request declares only pagination. *yields* decides whether an alternative in `from`
    produced anything, since only the caller knows what "items" means for its stage.
    """
    cache = cache if cache is not None else {}

    if request.page is not None:
        if request.page not in cache:
            raise FetchError(
                f"page: {request.page!r} has not been fetched yet. Stages run search, novel, "
                "toc, chapter, so a later one may reuse an earlier document but not the reverse"
            )
        return cache[request.page]

    if request.from_:
        return _first_that_yields(
            request.from_,
            fetcher,
            context,
            cache,
            default_url,
            parser,
            spec_headers,
            spec_encoding,
            yields,
        )

    merged = {**dict(spec_headers or {}), **request.headers}
    headers = {k: render(v, context) for k, v in merged.items()}
    encoding = request.encoding or spec_encoding

    document = _perform(request, fetcher, context, headers, encoding, default_url, parser, cache)
    if request.name:
        cache[request.name] = document
    return document


def _perform(
    request: Request,
    fetcher: Fetcher,
    context: Mapping[str, Any],
    headers: Mapping[str, str],
    encoding: Optional[str],
    default_url: Optional[str],
    parser: str,
    cache: Mapping[str, Document],
) -> Document:
    target = request.get or request.post
    url = render_url(target, context) if target else default_url
    if not url:
        raise FetchError("a request with no address needs a stage default")

    if request.render:
        return _as_document(fetcher.render(url, wait_for=request.wait_for), parser)

    if request.form is not None:
        return _post_a_form(request, fetcher, context, headers, encoding, url, parser, cache)

    if request.post is not None:
        kind, body = encode_payload(_render_payload(request.payload, context), headers)
        response = fetcher.fetch(
            "POST",
            url,
            headers=headers,
            form=body if kind == "form" else None,
            json=body if kind == "json" else None,
            encoding=encoding,
        )
        return _as_document(response, parser)

    return _as_document(fetcher.fetch("GET", url, headers=headers, encoding=encoding), parser)


def _post_a_form(
    request: Request,
    fetcher: Fetcher,
    context: Mapping[str, Any],
    headers: Mapping[str, str],
    encoding: Optional[str],
    url: str,
    parser: str,
    cache: Mapping[str, Document],
) -> Document:
    """The two readings of `form` in section 3.6.

    With `get`, the GET fetches the document holding the form and the POST goes to the form's
    own action. With `post`, the form comes from the document already in scope.
    """
    if request.get is not None:
        holder = _as_document(fetcher.fetch("GET", url, headers=headers, encoding=encoding), parser)
        target: Optional[str] = None
    else:
        holder = cache.get("novel") or cache.get("search")
        if holder is None:
            raise FetchError("form with post needs a document already in scope")
        target = url

    harvested, action = harvest_form(holder, request.form or "")
    harvested.update(_render_payload(request.payload, context))
    destination = target or action
    if not destination:
        raise FetchError("the harvested form declares no action and no post url was given")

    response = fetcher.fetch(
        "POST", destination, headers=headers, form=harvested, encoding=encoding
    )
    return _as_document(response, parser)


def _render_payload(payload: Mapping[str, Any], context: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: render(v, context) if isinstance(v, str) else v for k, v in payload.items()}


def _as_document(response: Fetched, parser: str) -> Document:
    return Document.from_html(
        response.text, url=response.url, parser=parser, headers=response.headers
    )


def _first_that_yields(
    alternatives: Sequence[Request],
    fetcher: Fetcher,
    context: Mapping[str, Any],
    cache: Dict[str, Document],
    default_url: Optional[str],
    parser: str,
    spec_headers: Optional[Mapping[str, str]],
    spec_encoding: Optional[str],
    yields: Optional[Callable[[Document], bool]],
) -> Document:
    last: Optional[Document] = None
    problems: List[str] = []
    for alternative in alternatives:
        try:
            document = run_request(
                alternative,
                fetcher,
                context,
                cache,
                default_url,
                parser,
                spec_headers,
                spec_encoding,
            )
        except Exception as error:
            problems.append(repr(error))
            continue
        last = document
        if yields is None or yields(document):
            return document
    if last is not None:
        return last
    raise FetchError("no alternative in `from` produced a document: " + "; ".join(problems))


def walk_pages(
    first: Document,
    paginate: Optional[Paginate],
    fetcher: Fetcher,
    context: Mapping[str, Any],
    count_items: Optional[Callable[[Document], int]] = None,
    parser: str = DEFAULT_PARSER,
    headers: Optional[Mapping[str, str]] = None,
    encoding: Optional[str] = None,
    workers: int = DEFAULT_WORKERS,
    limit: Optional[int] = None,
) -> Tuple[List[Document], bool]:
    """Every page of a paginated stage in order, and whether a limit truncated the result.

    The first page is the stage's own request; `paginate.url` produces the second onward,
    because sites routinely address the first page differently.

    *count_items* answers how many rows a page produced. A count rather than a boolean because
    `by: items` needs the number to address the page after it, and "did it yield anything" is
    the same question asked less precisely.
    """
    if paginate is None:
        return [first], False

    if paginate.next is not None:
        return _follow_links(first, paginate, fetcher, context, parser, headers, encoding, limit)
    if paginate.last is not None:
        return _fetch_numbered(
            first, paginate, fetcher, context, parser, headers, encoding, workers, limit
        )
    return _until_empty(
        first, paginate, fetcher, context, count_items, parser, headers, encoding, workers, limit
    )


def _page_url(paginate: Paginate, context: Mapping[str, Any], page: int, request_url: str) -> str:
    return render_url(
        paginate.url or "",
        {
            **dict(context),
            "page": page,
            "request_url": request_url,
        },
    )


def _number(value: Any, first: Document, fallback: int) -> int:
    """A page number a spec gave literally or as something to read off the first page.

    The largest match wins where an extractor produced several, because a pager lists every page it
    can reach and the last of them is the one that bounds the walk.
    """
    if value is None:
        return fallback
    if isinstance(value, int):
        return value

    raw = extract(value, first)
    numbers = []
    for item in raw if isinstance(raw, list) else [raw]:
        try:
            numbers.append(int(str(item).strip()))
        except (TypeError, ValueError):
            continue
    return max(numbers) if numbers else fallback


def _fetch_one(
    paginate: Paginate,
    fetcher: Fetcher,
    context: Mapping[str, Any],
    first_url: str,
    page: int,
    parser: str,
    headers: Optional[Mapping[str, str]],
    encoding: Optional[str],
) -> Document:
    url = _page_url(paginate, context, page, first_url)
    return _as_document(fetcher.fetch("GET", url, headers=headers, encoding=encoding), parser)


def _fetch_numbered(
    first: Document,
    paginate: Paginate,
    fetcher: Fetcher,
    context: Mapping[str, Any],
    parser: str,
    headers: Optional[Mapping[str, str]],
    encoding: Optional[str],
    workers: int,
    cap: Optional[int],
) -> Tuple[List[Document], bool]:
    # The stage's own request produced page `first`, so the walk covers the ones after it. Without
    # `first` the sequence was fixed at 2, 3, 4, and a site numbering from zero silently lost a page
    # while reporting success.
    origin = _number(paginate.first, first, 1)
    begin = origin + paginate.step
    end = _number(paginate.last, first, begin - paginate.step)
    wanted = list(range(begin, end + 1, paginate.step))

    truncated = bool(cap and len(wanted) + 1 > cap)
    if cap:
        wanted = wanted[: max(0, cap - 1)]
    if not wanted:
        return [first], truncated

    def one(page: int) -> Document:
        return _fetch_one(paginate, fetcher, context, first.url, page, parser, headers, encoding)

    if paginate.runs_concurrently:
        # Assembled by page index, never by completion. Ordering by completion would number
        # chapters differently on every run, and the numbering is what gets stored.
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            rest = list(pool.map(one, wanted))
    else:
        rest = [one(page) for page in wanted]

    return [first, *rest], truncated


def _until_empty(
    first: Document,
    paginate: Paginate,
    fetcher: Fetcher,
    context: Mapping[str, Any],
    count_items: Optional[Callable[[Document], int]],
    parser: str,
    headers: Optional[Mapping[str, str]],
    encoding: Optional[str],
    workers: int = DEFAULT_WORKERS,
    cap: Optional[int] = None,
) -> Tuple[List[Document], bool]:
    if count_items is None:
        raise FetchError("while: has_items needs a way to tell whether a page yielded rows")

    def has_items(document: Document) -> bool:
        return count_items(document) > 0

    pages = [first]
    step = paginate.step
    page = _number(paginate.first, first, 1) + step
    window = min(max(1, workers), SPECULATIVE_WORKERS) if paginate.runs_concurrently else 1

    while True:
        if cap and len(pages) >= cap:
            return pages, True

        wanted = list(range(page, page + window * step, step))
        if cap:
            wanted = wanted[: cap - len(pages)]

        # A window at a time rather than one page at a time. Nothing says how many pages there are,
        # so the walk is speculative: it asks for the next few together and keeps them only up to
        # the first that came back empty, discarding the rest. That costs at most one window of
        # requests past the end of the list, and turns a novel with a hundred pages of chapter list
        # from a hundred round trips into a few.
        #
        # Not a licence to flood a host: the pace a spec asks for is applied per origin underneath,
        # so a window shares one budget rather than each request getting its own.
        batch = _fetch_window(
            wanted, paginate, fetcher, context, first.url, parser, headers, encoding, window
        )

        for document in batch:
            if document is None or not has_items(document):
                return pages, False
            pages.append(document)

        if len(batch) < len(wanted):
            return pages, bool(cap and len(pages) >= cap)
        page += len(wanted) * step


def _fetch_window(
    wanted: Sequence[int],
    paginate: Paginate,
    fetcher: Fetcher,
    context: Mapping[str, Any],
    first_url: str,
    parser: str,
    headers: Optional[Mapping[str, str]],
    encoding: Optional[str],
    workers: int,
) -> List[Optional[Document]]:
    """The pages in *wanted*, in order, stopping the list at the first that could not be fetched.

    A failure is `None` rather than an exception, because with `while` the page after the last one
    is *expected* to fail or come back empty: that is how the walk learns where the end is.
    """

    def one(page: int) -> Optional[Document]:
        try:
            return _fetch_one(
                paginate, fetcher, context, first_url, page, parser, headers, encoding
            )
        except Exception:
            return None

    if len(wanted) == 1 or workers <= 1:
        return [one(wanted[0])] if wanted else []

    with ThreadPoolExecutor(max_workers=min(workers, len(wanted))) as pool:
        return list(pool.map(one, wanted))


def _follow_links(
    first: Document,
    paginate: Paginate,
    fetcher: Fetcher,
    context: Mapping[str, Any],
    parser: str,
    headers: Optional[Mapping[str, str]],
    encoding: Optional[str],
    cap: Optional[int] = None,
) -> Tuple[List[Document], bool]:
    pages = [first]
    seen = {first.url}
    current = first
    while True:
        if cap and len(pages) >= cap:
            return pages, True
        link = extract(paginate.next, current, kind="url") if paginate.next else None
        target = link[0] if isinstance(link, list) and link else link
        if not target or not isinstance(target, str):
            return pages, False
        # A spec's own regex handles a site reusing one link for "next page" and "next
        # chapter"; a loop back to a page already seen is handled here.
        if target in seen:
            return pages, False
        seen.add(target)
        current = _as_document(
            fetcher.fetch("GET", target, headers=headers, encoding=encoding), parser
        )
        pages.append(current)
