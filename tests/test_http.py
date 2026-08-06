"""The scraper-backed Fetcher.

Tested against a stand-in session rather than the real scraper, because what matters here is
the adaptation: which arguments reach the layer below, and what comes back.
"""

import pytest

from sourcelib.fetch import FetchError
from sourcelib.http import ScraperFetcher, requires_scraper, solver


class FakeResponse:
    def __init__(self, url, text, headers=None, status_code=200):
        self.url = url
        self.text = text
        self.headers = headers or {}
        self.status_code = status_code
        self.encoding = "utf-8"


class FakeSession:
    def __init__(self):
        self.fetched = []
        self.rendered = []
        self.closed = False

    def fetch(self, method, url, **options):
        self.fetched.append((method, url, options))
        return FakeResponse(url, "<html>ok</html>", {"x-total": "3"})

    def render(self, url, wait_for=None):
        self.rendered.append((url, wait_for))
        return "<html>rendered</html>"

    def close(self):
        self.closed = True


@pytest.fixture
def session():
    return FakeSession()


class TestFetch:
    def test_a_get_passes_only_what_it_needs(self, session):
        ScraperFetcher(session).fetch("GET", "https://e/x")
        method, url, options = session.fetched[0]
        assert (method, url) == ("GET", "https://e/x")
        assert options == {}

    def test_headers_are_forwarded(self, session):
        ScraperFetcher(session).fetch("GET", "https://e/x", headers={"x-a": "b"})
        assert session.fetched[0][2]["headers"] == {"x-a": "b"}

    def test_a_form_body_becomes_data(self, session):
        ScraperFetcher(session).fetch("POST", "https://e/x", form={"q": "a"})
        assert session.fetched[0][2]["data"] == {"q": "a"}
        assert "json" not in session.fetched[0][2]

    def test_a_json_body_stays_json(self, session):
        ScraperFetcher(session).fetch("POST", "https://e/x", json={"q": True})
        assert session.fetched[0][2]["json"] == {"q": True}
        assert "data" not in session.fetched[0][2]

    def test_the_method_is_upper_cased(self, session):
        ScraperFetcher(session).fetch("get", "https://e/x")
        assert session.fetched[0][0] == "GET"

    def test_the_response_is_reduced_to_what_a_spec_reads(self, session):
        result = ScraperFetcher(session).fetch("GET", "https://e/x")
        assert result.url == "https://e/x"
        assert result.text == "<html>ok</html>"
        assert result.headers == {"x-total": "3"}
        assert result.status == 200

    def test_an_encoding_is_applied_to_the_response(self, session):
        # A site serving a legacy encoding usually declares none, or declares it wrongly.
        class Recording(FakeSession):
            def fetch(self, method, url, **options):
                self.response = FakeResponse(url, "text")
                return self.response

        recording = Recording()
        ScraperFetcher(recording).fetch("GET", "https://e/x", encoding="gbk")
        assert recording.response.encoding == "gbk"


class TestRender:
    def test_it_passes_the_wait_selector_through(self, session):
        result = ScraperFetcher(session).render("https://e/app", wait_for=".ready")
        assert session.rendered == [("https://e/app", ".ready")]
        assert result.text == "<html>rendered</html>"

    def test_it_works_without_a_wait_selector(self, session):
        ScraperFetcher(session).render("https://e/app")
        assert session.rendered == [("https://e/app", None)]


class TestLifecycle:
    def test_it_closes_the_session(self, session):
        ScraperFetcher(session).close()
        assert session.closed

    def test_it_is_a_context_manager(self, session):
        with ScraperFetcher(session) as fetcher:
            assert fetcher.session is session
        assert session.closed

    def test_closing_a_session_without_close_is_harmless(self):
        class Bare:
            def fetch(self, *a, **k):  # pragma: no cover - never called
                raise AssertionError

        ScraperFetcher(Bare()).close()


class TestMissingExtra:
    def test_it_explains_how_to_install_the_extra(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name == "scraper":
                raise ImportError("no scraper")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)
        with pytest.raises(FetchError, match=r"lncrawl-sourcelib\[fetch\]"):
            requires_scraper()


class TestBrowserSolver:
    """A configured browser is what makes `render` and a challenged host reachable at all."""

    def install(self, monkeypatch, chromium, solver_class):
        import sys
        from types import SimpleNamespace

        # `from x import y` reads an attribute off whatever sys.modules holds, so a namespace
        # stands in for the module without the extra being installed.
        monkeypatch.setitem(
            sys.modules, "scraper.browsers", SimpleNamespace(pick_chromium=lambda: chromium)
        )
        monkeypatch.setitem(sys.modules, "scraper.cdp", SimpleNamespace(CdpSolver=solver_class))
        # The transport too: `scraper.cdp` imports without it and only needs it once it drives a
        # browser, so "this machine can run one" is not answered by the module alone.
        monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace())

    def test_a_machine_with_a_browser_gets_a_solver(self, monkeypatch):
        class Solver:
            def __init__(self, **options):
                self.options = options

        self.install(monkeypatch, "/usr/bin/chromium", Solver)
        made = solver()
        assert isinstance(made, Solver)
        # Hidden first, a window only where one would be seen and only when it is needed.
        assert made.options == {"mode": "auto"}

    def test_without_the_transport_it_is_absent(self, monkeypatch):
        import builtins
        import sys
        from types import SimpleNamespace

        monkeypatch.setitem(
            sys.modules,
            "scraper.browsers",
            SimpleNamespace(pick_chromium=lambda: "/usr/bin/chromium"),
        )
        monkeypatch.setitem(sys.modules, "scraper.cdp", SimpleNamespace(CdpSolver=object))
        monkeypatch.delitem(sys.modules, "websockets", raising=False)

        real = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name == "websockets":
                raise ImportError(name)
            return real(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)
        # Built anyway, the solver would raise on its first call, and a caller reading that as
        # "the host refused us" disables a site the browser never tried.
        assert solver() is None

    def test_no_browser_installed_is_not_an_error(self, monkeypatch):
        self.install(monkeypatch, None, object)
        # A browser is heavy and most runs never need one, so absent must not refuse to fetch.
        assert solver() is None

    def test_without_the_extra_it_is_absent(self, monkeypatch):
        import builtins

        real = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name.startswith("scraper."):
                raise ImportError(name)
            return real(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)
        assert solver() is None
