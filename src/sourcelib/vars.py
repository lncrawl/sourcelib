"""Named values a spec reads once and reuses, per RFC-0001 sections 3.5 and 4.5.

Each var is cached for the lifetime of what it reads, and evaluation is single-flight. Both
matter under load rather than in a single-threaded run, which is why they are here rather than
left to the caller.

A chapter-scoped var cached for the session would reuse a value read from the first chapter for
every later one, and that reads as intermittent site trouble rather than as a caching bug. A var
backed by its own request without single-flight is fetched once per worker thread, which is
wasteful and a good way to be rate-limited on the first page of every crawl.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Mapping, Optional

from sourcelib.spec.extract import Document, extract
from sourcelib.spec.model import Request, Var

__all__ = ["VarCache", "VarError"]

#: What each `on` value is cached against.
SESSION, NOVEL, CHAPTER = "session", "novel", "chapter"

_SCOPE_OF = {"url": NOVEL, "novel": NOVEL, "chapter": CHAPTER}


class VarError(Exception):
    """A var could not be evaluated, or was read where its scope does not reach."""


class VarCache:
    """Evaluates and caches a spec's vars, one instance per crawl.

    Held by the interpreter and passed where it is needed. Nothing is looked up ambiently,
    because a spec is shared by every crawl of its host and only an argument can say which
    crawl a value belongs to.
    """

    def __init__(
        self,
        declared: Mapping[str, Var],
        origin: str,
        fetch_document: Optional[Callable[[Request], Document]] = None,
    ) -> None:
        self.declared = dict(declared)
        self.origin = origin
        self._fetch_document = fetch_document
        self._values: Dict[str, Dict[str, Any]] = {SESSION: {}, NOVEL: {}, CHAPTER: {}}
        self._locks: Dict[str, threading.Lock] = {name: threading.Lock() for name in declared}
        self._documents: Dict[str, Document] = {}
        self._novel_url = ""

    # -- what the vars read ----------------------------------------------------------- #

    def begin_novel(self, url: str, document: Optional[Document] = None) -> None:
        """Start a novel, discarding anything scoped to the previous one."""
        self._novel_url = url
        self._values[NOVEL] = {}
        self._values[CHAPTER] = {}
        if document is not None:
            self._documents[NOVEL] = document

    def begin_chapter(self, document: Optional[Document] = None) -> None:
        """Start a chapter, discarding the previous chapter's values."""
        self._values[CHAPTER] = {}
        if document is not None:
            self._documents[CHAPTER] = document

    def offer(self, name: str, document: Document) -> None:
        """Record a stage document so a var reading it need not fetch its own."""
        self._documents[name] = document

    # -- reading ---------------------------------------------------------------------- #

    def get(self, name: str) -> Any:
        """The value of one var, evaluating it at most once per scope."""
        spec = self.declared.get(name)
        if spec is None:
            raise VarError(f"unknown var {name!r}; declared: {sorted(self.declared) or 'none'}")

        scope = self._scope_of(spec)
        cached = self._values[scope]
        if name in cached:
            return cached[name]

        # Single-flight. Without it a token-bearing var is fetched once per worker thread.
        with self._locks.setdefault(name, threading.Lock()):
            if name in cached:
                return cached[name]
            value = self._evaluate(name, spec)
            cached[name] = value
            return value

    def renew(self, name: str) -> Any:
        """Discard a cached value and evaluate it again, for a credential that expired."""
        spec = self.declared.get(name)
        if spec is None:
            raise VarError(f"unknown var {name!r}")
        if not spec.renew:
            raise VarError(
                f"var {name!r} does not declare renew, so a stale value is an error rather "
                "than something to retry: retrying on every failure would mask a wrong selector"
            )
        with self._locks.setdefault(name, threading.Lock()):
            self._values[self._scope_of(spec)].pop(name, None)
        return self.get(name)

    def as_mapping(self) -> Mapping[str, Any]:
        """A lazy view for templates, so an unused var is never evaluated."""
        return _LazyVars(self)

    # -- internals -------------------------------------------------------------------- #

    @staticmethod
    def _scope_of(spec: Var) -> str:
        if isinstance(spec.on, Request):
            return SESSION
        return _SCOPE_OF.get(str(spec.on), NOVEL)

    def _evaluate(self, name: str, spec: Var) -> Any:
        if isinstance(spec.on, Request):
            if self._fetch_document is None:
                raise VarError(f"var {name!r} needs its own request, but no fetcher was given")
            document = self._fetch_document(spec.on)
            return self._read(spec, document)

        where = str(spec.on)
        if where == "url":
            # No document at all: the identifier is in the URL string itself.
            return self._read(spec, Document(url=self._novel_url, text=self._novel_url))

        document = self._documents.get(where)
        if document is None:
            raise VarError(f"var {name!r} reads the {where} page, which has not been fetched yet")
        return self._read(spec, document)

    def _read(self, spec: Var, document: Document) -> Any:
        return extract(
            spec.__class__.model_construct(**spec.model_dump(by_alias=False)),
            document,
        )


class _LazyVars(Mapping[str, Any]):
    """Reads a var on first access, so declaring one costs nothing until it is used."""

    def __init__(self, cache: VarCache) -> None:
        self._cache = cache

    def __getitem__(self, key: str) -> Any:
        return self._cache.get(key)

    def __iter__(self):
        return iter(self._cache.declared)

    def __len__(self) -> int:
        return len(self._cache.declared)

    def __contains__(self, key: object) -> bool:
        return key in self._cache.declared
