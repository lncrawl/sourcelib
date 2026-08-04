"""The scraper-backed Fetcher.

Imported only when fetching is actually wanted, so `pip install lncrawl-sourcelib` stays light
enough for a spec author and for the definitions repository's CI. Everything else in this
package works without it.

The HTTP layer below learns per origin what works, so nothing here configures addresses,
impersonation profiles or pacing. A spec declares facts about a site's *content*, and the
layer discovers how to reach it.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from sourcelib.fetch import Fetched, FetchError

__all__ = ["ScraperFetcher", "requires_scraper"]

_MISSING = "fetching needs the scraper. Install it with: pip install 'lncrawl-sourcelib[fetch]'"


def requires_scraper() -> Any:
    """Import scraper, or explain how to get it."""
    try:
        # An optional extra, so a type checker cannot resolve it in a base install. Ignored
        # here rather than by adding it as a hard dependency, which would make every spec
        # author install an HTTP stack to validate YAML.
        import scraper  # type: ignore[import-not-found]
    except ImportError as error:  # pragma: no cover - depends on the installed extras
        raise FetchError(_MISSING) from error
    return scraper


class ScraperFetcher:
    """Adapts `scraper.Scraper` to the protocol the fetch engine expects.

    Deliberately thin. Rate limiting, retries, address rotation and challenge solving all
    belong to the layer underneath, and reimplementing any of them here would compete with
    state it has already learned.
    """

    def __init__(self, session: Any = None, origin: str = "", rate_limit: float = 0.0) -> None:
        if session is None:
            scraper = requires_scraper()
            session = scraper.Scraper(origin=origin)
            if origin and rate_limit > 0:
                # Pace by origin rather than per call, so concurrent pages share one budget.
                session.state.pacer.learn(session.state.memory.key(origin), 1.0 / rate_limit)
        self.session = session

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
        options: dict = {}
        if headers:
            options["headers"] = dict(headers)
        if form is not None:
            options["data"] = dict(form)
        if json is not None:
            options["json"] = json

        response = self.session.fetch(method.upper(), url, **options)
        if encoding:
            # A site serving a legacy encoding usually declares none, or declares it wrongly.
            response.encoding = encoding
        return Fetched(
            url=str(response.url),
            text=response.text,
            headers=dict(response.headers),
            status=int(response.status_code),
        )

    def render(self, url: str, *, wait_for: Optional[str] = None) -> Fetched:
        """Run the page's scripts and read what they produced.

        Far slower and heavier than an HTTP fetch, so a spec should set `render` only on a
        stage that does not work without it.
        """
        markup = self.session.render(url, wait_for=wait_for)
        return Fetched(url=url, text=markup, headers={}, status=200)

    def close(self) -> None:
        closer = getattr(self.session, "close", None)
        if callable(closer):
            closer()

    def __enter__(self) -> "ScraperFetcher":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
