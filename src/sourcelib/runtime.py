"""Running a resolved spec against a site, per RFC-0001 sections 3.8, 4.4 and 6.5.

This is where the pieces meet: a request produces a document, extractors read it, a pipe
cleans the values, and the result is a model. Everything below it is independently testable,
so this layer stays a sequence of small steps rather than a place where behaviour hides.

Failure is deliberately uneven, following section 4.4. A missing title or an empty chapter list
is an error, because the result would be useless. A missing cover, author, tag list or synopsis
is a warning, because real pages omit them often enough that failing would reject working
sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Collection, Dict, List, Mapping, Optional, Tuple

from sourcelib.fetch import Fetcher, run_request, walk_pages
from sourcelib.hooks import Context, HookRegistry
from sourcelib.interpolate import render
from sourcelib.models import Chapter, Novel, SearchResult, Volume
from sourcelib.spec.extract import Document, extract
from sourcelib.spec.items import Row, assign_volumes, group_by_size, read_rows, sort_rows
from sourcelib.spec.model import Extractor, ItemList, SourceSpec
from sourcelib.vars import VarCache

__all__ = ["CrawlError", "Interpreter", "Report"]

#: Which default pipe each named field takes (section 6.4).
_KINDS = {
    "title": "title",
    "cover": "cover",
    "authors": "authors",
    "tags": "tags",
    "synopsis": "synopsis",
    "url": "url",
    "body": "body",
}

#: OpenGraph, then JSON-LD, then the document title (section 3.8).
_META_FALLBACKS: Dict[str, List[Dict[str, Any]]] = {
    "title": [
        {"css": 'meta[property="og:title"]', "attr": "content"},
        {"css": 'script[type="application/ld+json"]', "json": "name"},
        {"css": "title"},
    ],
    "cover": [
        {"css": 'meta[property="og:image"]', "attr": "content"},
        {"css": 'script[type="application/ld+json"]', "json": "image"},
    ],
    "synopsis": [
        {"css": 'meta[property="og:description"]', "attr": "content"},
        {"css": 'meta[name="description"]', "attr": "content"},
        {"css": 'script[type="application/ld+json"]', "json": "description"},
    ],
    "authors": [{"css": 'script[type="application/ld+json"]', "json": "author.name"}],
}


class CrawlError(Exception):
    """A stage could not produce a required value, named by the spec field responsible."""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(f"{field_name}: {message}")
        self.field = field_name


@dataclass
class Report:
    """What a run noticed but did not fail on."""

    warnings: List[str] = field(default_factory=list)
    #: Rows dropped per stage. A large number means the selector is wrong even though the
    #: crawl succeeded, which is the most common defect in the corpus.
    skipped: Dict[str, int] = field(default_factory=dict)
    #: Stages where a `limit` cut the result short, since a silent cap is indistinguishable
    #: from a site with fewer pages.
    truncated: List[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


class Interpreter:
    """Crawls one host with one resolved spec.

    One instance per crawl. The spec itself is shared and never mutated, so everything
    per-crawl lives here.
    """

    def __init__(
        self,
        spec: SourceSpec,
        fetcher: Fetcher,
        report: Optional[Report] = None,
        hooks: Optional[Mapping[str, Tuple[Callable[..., Any], str]]] = None,
    ) -> None:
        self.spec = spec
        self.fetcher = fetcher
        self.report = report or Report()
        self.origin = _origin_of(spec)
        self.documents: Dict[str, Document] = {}
        self.vars = VarCache(spec.vars, self.origin, self._fetch_for_var)
        self.hooks = dict(hooks or {})
        # One Context per crawl, passed into every hook call. Never ambient: a hook module is
        # shared by every crawl, so only an argument can say which crawl it is serving.
        self.ctx = Context(fetcher, spec, self.vars.as_mapping())

    @classmethod
    def load(
        cls,
        spec: SourceSpec,
        fetcher: Fetcher,
        root: Optional[Path] = None,
        report: Optional[Report] = None,
    ) -> "Interpreter":
        """Build an interpreter, binding the spec's hooks from *root*."""
        bound = HookRegistry(root).bind(spec.hooks) if (root and spec.hooks) else {}
        return cls(spec, fetcher, report, bound)

    # -- hooks ------------------------------------------------------------------------- #

    def _hook(self, point: str):
        """The function bound to *point*, and a context labelled with its owner."""
        entry = self.hooks.get(point)
        if entry is None:
            return None, None
        function, owner = entry
        return function, self.ctx.for_owner(owner)

    def _hooked(self, point: str, value: Any, document: Optional[Document]) -> Any:
        """Run a transform-shaped hook, or return *value* unchanged when none is bound."""
        function, ctx = self._hook(point)
        if function is None:
            return value
        return function(value, document, ctx)

    # -- context ---------------------------------------------------------------------- #

    def context(self, **extra: Any) -> Dict[str, Any]:
        base: Dict[str, Any] = {"origin": self.origin, "vars": self.vars.as_mapping()}
        base.update({k: v for k, v in extra.items() if v is not None})
        return base

    def _fetch_for_var(self, request: Any) -> Document:
        return run_request(
            request,
            self.fetcher,
            {"origin": self.origin, "vars": {}},
            cache={},
            parser=self.spec.parser or "html.parser",
            spec_headers=self.spec.headers,
            spec_encoding=self.spec.encoding,
        )

    def _run(self, stage: str, request: Any, default_url: Optional[str] = None, **ctx: Any):
        function, hook_ctx = self._hook(f"{stage}.request")
        if function is not None:
            # A request hook replaces the whole fetch, which is how a site speaking a protocol
            # nothing here models still produces a document.
            document = function(default_url or "", hook_ctx)
        else:
            document = run_request(
                request,
                self.fetcher,
                self.context(**ctx),
                cache=self.documents,
                default_url=default_url,
                parser=self.spec.parser or "html.parser",
                spec_headers=self.spec.headers,
                spec_encoding=self.spec.encoding,
                yields=None,
            )
        self.documents[stage] = document
        self.vars.offer(stage, document)
        return document

    # -- search ----------------------------------------------------------------------- #

    def search(self, query: str) -> List[SearchResult]:
        """Search results for *query*, or an empty list when the source cannot search."""
        stage = self.spec.search
        if stage is None or stage.request is None:
            return []

        document = self._run("search", stage.request, query=query)
        pages, truncated = walk_pages(
            document,
            stage.request.paginate,
            self.fetcher,
            self.context(query=query),
            has_items=lambda d: bool(self._rows(stage, d, ("url",))[0]),
            parser=self.spec.parser or "html.parser",
        )
        if truncated:
            self.report.truncated.append("search")

        results: List[SearchResult] = []
        for page in pages:
            rows, skipped = self._rows(stage, page, ("url",))
            if skipped:
                self.report.skipped["search"] = self.report.skipped.get("search", 0) + skipped
            for row in rows:
                results.append(
                    SearchResult(
                        title=_text(row.get("title")),
                        url=_text(row.get("url")),
                        info=_text(row.get("info")),
                        extras=_extras(row, ("title", "url", "info")),
                    )
                )
        return results

    # -- novel ------------------------------------------------------------------------ #

    def read_novel(self, url: str) -> Novel:
        """The novel at *url*, with its table of contents."""
        self.vars.begin_novel(url)
        novel = Novel(url=url, is_manga=self.spec.has_manga, is_mtl=self.spec.has_mtl)

        stage = self.spec.novel
        request = stage.request if stage else None
        document = self._run("novel", request or _get(url), default_url=url, novel_url=url)

        novel.title = _text(self._field(stage, "title", document))
        if not novel.title:
            raise CrawlError("novel.title", "produced nothing, and a novel needs a title")

        novel.cover_url = _text(self._field(stage, "cover", document))
        novel.authors = _as_list(self._field(stage, "authors", document))
        novel.tags = _as_list(self._field(stage, "tags", document))
        novel.synopsis = _text(self._field(stage, "synopsis", document))
        novel.language = self._language(stage, document)

        for name, value in (
            ("cover", novel.cover_url),
            ("authors", novel.authors),
            ("tags", novel.tags),
            ("synopsis", novel.synopsis),
        ):
            if not value:
                self.report.warn(f"novel.{name} produced nothing")

        self._read_toc(novel, url)
        return novel

    def _field(self, stage: Any, name: str, document: Document) -> Any:
        """One novel field, falling back to standard page metadata when the spec is silent.

        A hook bound to this field runs last, over whatever the spec or the metadata produced.
        """
        value = self._read_field(stage, name, document)
        return self._hooked(f"novel.{name}", value, document)

    def _read_field(self, stage: Any, name: str, document: Document) -> Any:
        declared = getattr(stage, name, None) if stage else None
        if declared is not None:
            value = extract(declared, document, kind=_KINDS.get(name), pipes=self.spec.pipes)
            if not _blank(value):
                return value

        for alternative in _META_FALLBACKS.get(name, []):
            value = extract(
                Extractor.model_validate(alternative),
                document,
                kind=_KINDS.get(name),
                pipes=self.spec.pipes,
            )
            if not _blank(value):
                return value
        return None

    def _language(self, stage: Any, document: Document) -> Optional[str]:
        """Detection beats declaration, per section 3.2's precedence table."""
        detected = extract(
            Extractor.model_validate(
                {
                    "css": "html[lang]",
                    "attr": "lang",
                    "fallback": [{"css": 'meta[property="og:locale"]', "attr": "content"}],
                }
            ),
            document,
        )
        if isinstance(detected, str) and detected.strip():
            return detected.strip().replace("_", "-").split("-")[0].lower()

        declared = getattr(stage, "language", None) if stage else None
        if declared is not None:
            value = extract(declared, document, pipes=self.spec.pipes)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()

        return self.spec.language

    # -- table of contents ------------------------------------------------------------ #

    def _read_toc(self, novel: Novel, url: str) -> None:
        stage = self.spec.toc
        hooked_items, _ = self._hook("toc.items")
        if hooked_items is not None:
            return self._hooked_toc(novel, url, stage)
        if stage is None or stage.items is None:
            raise CrawlError("toc.items", "a novel needs a chapter list")

        chapter_list = stage.items
        request = stage.request or _get(url)
        document = self._run("toc", request, default_url=url, novel_url=url)
        pages, truncated = walk_pages(
            document,
            request.paginate,
            self.fetcher,
            self.context(novel_url=url),
            has_items=lambda d: bool(self._rows(chapter_list, d, ("url",))[0]),
            parser=self.spec.parser or "html.parser",
        )
        if truncated:
            self.report.truncated.append("toc")

        rows: List[Row] = []
        titles: Dict[int, str] = {}
        for page in pages:
            page_rows, skipped = read_rows(
                stage.items,
                page,
                required=("url",),
                kinds=_KINDS,
                pipes=self.spec.pipes,
                start=len(rows),
            )
            if skipped:
                self.report.skipped["toc"] = self.report.skipped.get("toc", 0) + skipped
            if stage.volumes is not None:
                heading_rows, _ = read_rows(
                    stage.volumes, page, kinds=_KINDS, pipes=self.spec.pipes
                )
                found = assign_volumes(page, stage.items, stage.volumes, page_rows, heading_rows)
                offset = max(titles) if titles else 0
                for number, title in found.items():
                    titles[number + offset] = title
                    for row in page_rows:
                        if row.get("volume") == number:
                            row.fields["volume"] = number + offset
            rows.extend(page_rows)

        if not rows:
            raise CrawlError("toc.items", "matched no chapters")

        if stage.volumes is None:
            group_by_size(rows, self.spec.chapters_per_volume)

        # Sorting runs after volume assignment, because assignment is positional in document
        # order and a sorted list has no positions to read.
        rows = sort_rows(rows, stage.items)

        seen: Dict[int, Volume] = {}
        for index, row in enumerate(rows, start=1):
            number = _as_int(row.get("volume")) or 1
            if number not in seen:
                seen[number] = Volume(id=number, title=titles.get(number, ""))
            novel.chapters.append(
                Chapter(
                    id=index,
                    url=_text(row.get("url")),
                    title=_text(row.get("title")) or f"Chapter {index}",
                    volume=number,
                    extras=_extras(row, ("title", "url", "volume")),
                )
            )
        novel.volumes = [seen[key] for key in sorted(seen)]

    def _rows(self, item_list: ItemList, document: Document, required):
        return read_rows(
            item_list, document, required=required, kinds=_KINDS, pipes=self.spec.pipes
        )

    def _hooked_toc(self, novel: Novel, url: str, stage: Any) -> None:
        """A table of contents a hook produces, for a site whose list is not a document.

        The hook returns rows as mappings. Volume grouping still applies, so a hook-driven list
        can carry its own `volume` on each row or fall back to `chapters_per_volume`.
        """
        request = (stage.request if stage else None) or _get(url)
        document = self._run("toc", request, default_url=url, novel_url=url)
        function, ctx = self._hook("toc.items")
        produced = function([], document, ctx) if function else []

        rows = [Row(dict(entry)) for entry in produced or []]
        if not rows:
            raise CrawlError("toc.items", "the hook produced no chapters")

        titles = self._hooked_volumes(document, rows)
        if not titles:
            group_by_size(rows, self.spec.chapters_per_volume)

        seen: Dict[int, Volume] = {}
        for index, row in enumerate(rows, start=1):
            number = _as_int(row.get("volume")) or 1
            if number not in seen:
                seen[number] = Volume(id=number, title=titles.get(number, ""))
            novel.chapters.append(
                Chapter(
                    id=index,
                    url=_text(row.get("url")),
                    title=_text(row.get("title")) or f"Chapter {index}",
                    volume=number,
                    extras=_extras(row, ("title", "url", "volume")),
                )
            )
        novel.volumes = [seen[key] for key in sorted(seen)]

    def _hooked_volumes(self, document: Document, rows: List[Row]) -> Dict[int, str]:
        """Volumes a hook supplies, which a hook-driven list previously could not express."""
        function, ctx = self._hook("toc.volumes")
        if function is None:
            return {}
        produced = function([], document, ctx) or []
        return {
            number: str(entry.get("title", "")) for number, entry in enumerate(produced, start=1)
        }

    # -- chapter ---------------------------------------------------------------------- #

    def download_chapter(self, novel: Novel, chapter: Chapter) -> Chapter:
        """Fill in *chapter*'s body."""
        stage = self.spec.chapter
        body_hook, _ = self._hook("chapter.body")
        if stage is None or (stage.body is None and body_hook is None):
            raise CrawlError("chapter.body", "a chapter needs a body")

        url = self._chapter_url(stage, chapter, novel)
        request = stage.request or _get(url)
        context = self.context(novel_url=novel.url, chapter=chapter.context())

        document = run_request(
            request,
            self.fetcher,
            context,
            cache=dict(self.documents),
            default_url=url,
            parser=self.spec.parser or "html.parser",
            spec_headers=self.spec.headers,
            spec_encoding=self.spec.encoding,
        )
        self.vars.begin_chapter(document)

        pages, truncated = walk_pages(
            document,
            request.paginate,
            self.fetcher,
            context,
            parser=self.spec.parser or "html.parser",
        )
        if truncated:
            self.report.truncated.append(f"chapter {chapter.id}")

        parts = []
        for page in pages:
            raw = (
                extract(stage.body, page, kind="body", pipes=self.spec.pipes)
                if stage.body is not None
                else None
            )
            # The hook runs per page, so a body split across pages is decrypted page by page.
            parts.append(_text(self._hooked("chapter.body", raw, page)))

        chapter.body = stage.join.join(part for part in parts if part)
        chapter.success = bool(chapter.body)
        if not chapter.success:
            raise CrawlError("chapter.body", "produced nothing")
        return chapter

    def _chapter_url(self, stage: Any, chapter: Chapter, novel: Novel) -> str:
        if stage.url is None:
            return chapter.url
        if isinstance(stage.url, str):
            return render(stage.url, self.context(novel_url=novel.url, chapter=chapter.context()))
        document = self.documents.get("toc")
        if document is None:
            return chapter.url
        return _text(extract(stage.url, document, kind="url", pipes=self.spec.pipes))


# -- helpers -------------------------------------------------------------------------- #


def _get(url: str):
    from sourcelib.spec.model import Request

    return Request.model_validate({"get": url})


def _origin_of(spec: SourceSpec) -> str:
    from urllib.parse import urlsplit

    if not spec.base_url:
        return ""
    parts = urlsplit(spec.base_url)
    return f"{parts.scheme}://{parts.netloc}"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(v) for v in value if v)
    return str(value)


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _extras(row: Row, named: Collection[str]) -> Dict[str, Any]:
    return {k: v for k, v in row.fields.items() if k not in named}
