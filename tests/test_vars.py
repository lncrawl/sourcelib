"""Var scoping, caching and single-flight, per RFC-0001 sections 3.5 and 4.5."""

import threading

import pytest

from sourcelib.spec.extract import Document
from sourcelib.spec.model import Request, Var
from sourcelib.vars import VarCache, VarError

ORIGIN = "https://e.test"


def var(**fields):
    return Var.model_validate(fields)


class TestScopes:
    def test_a_url_var_needs_no_document(self):
        cache = VarCache({"nid": var(on="url", regex=r"/novel/(\d+)")}, ORIGIN)
        cache.begin_novel("https://e.test/novel/1234/")
        assert cache.get("nid") == "1234"

    def test_a_novel_var_reads_the_novel_page(self):
        cache = VarCache({"mid": var(on="novel", css="#h", attr="data-id")}, ORIGIN)
        cache.begin_novel(
            "https://e.test/novel/x", Document.from_html('<div id="h" data-id="99"></div>')
        )
        assert cache.get("mid") == "99"

    def test_a_chapter_var_reads_the_chapter_page(self):
        cache = VarCache({"key": var(on="chapter", css="#k")}, ORIGIN)
        cache.begin_novel("https://e.test/novel/x")
        cache.begin_chapter(Document.from_html('<div id="k">abc</div>'))
        assert cache.get("key") == "abc"

    def test_reading_a_page_that_has_not_been_fetched_is_reported(self):
        cache = VarCache({"mid": var(on="novel", css="#h")}, ORIGIN)
        cache.begin_novel("https://e.test/novel/x")
        with pytest.raises(VarError, match="has not been fetched yet"):
            cache.get("mid")

    def test_an_unknown_var_lists_what_is_declared(self):
        cache = VarCache({"a": var(on="url", regex="x")}, ORIGIN)
        with pytest.raises(VarError, match="declared: \\['a'\\]"):
            cache.get("b")


class TestCaching:
    def test_a_session_var_is_evaluated_once(self):
        calls = []

        def fetch(request):
            calls.append(request.get)
            return Document.from_json({"token": "t0k"})

        cache = VarCache(
            {"tok": var(on={"get": "{origin}/api/token"}, json="token")}, ORIGIN, fetch
        )
        cache.begin_novel("https://e.test/a")
        assert cache.get("tok") == "t0k"
        cache.begin_novel("https://e.test/b")
        # Still cached across novels, because its scope is the session.
        assert cache.get("tok") == "t0k"
        assert len(calls) == 1

    def test_a_novel_var_is_discarded_when_the_novel_changes(self):
        cache = VarCache({"mid": var(on="novel", css="#h")}, ORIGIN)
        cache.begin_novel("https://e/a", Document.from_html('<div id="h">first</div>'))
        assert cache.get("mid") == "first"
        cache.begin_novel("https://e/b", Document.from_html('<div id="h">second</div>'))
        assert cache.get("mid") == "second"

    def test_a_chapter_var_is_discarded_between_chapters(self):
        # Caching this for the session would reuse chapter one's value for every chapter, and
        # that reads as intermittent site trouble rather than a caching bug.
        cache = VarCache({"k": var(on="chapter", css="#k")}, ORIGIN)
        cache.begin_novel("https://e/a")
        cache.begin_chapter(Document.from_html('<div id="k">one</div>'))
        assert cache.get("k") == "one"
        cache.begin_chapter(Document.from_html('<div id="k">two</div>'))
        assert cache.get("k") == "two"

    def test_a_novel_var_survives_a_chapter_change(self):
        cache = VarCache({"mid": var(on="novel", css="#h")}, ORIGIN)
        cache.begin_novel("https://e/a", Document.from_html('<div id="h">v</div>'))
        assert cache.get("mid") == "v"
        cache.begin_chapter(Document.from_html("<div/>"))
        assert cache.get("mid") == "v"


class TestSingleFlight:
    def test_concurrent_readers_evaluate_a_var_once(self):
        started = threading.Barrier(8)
        calls = []
        lock = threading.Lock()

        def fetch(request):
            with lock:
                calls.append(1)
            return Document.from_json({"token": "t0k"})

        cache = VarCache({"tok": var(on={"get": "{origin}/t"}, json="token")}, ORIGIN, fetch)
        cache.begin_novel("https://e/a")

        results = []

        def read():
            started.wait()
            results.append(cache.get("tok"))

        threads = [threading.Thread(target=read) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Eight threads, one fetch. Without single-flight this is one per worker, which is a
        # burst against the one endpoint that must not be antagonised.
        assert len(calls) == 1
        assert results == ["t0k"] * 8


class TestRenew:
    def test_renew_discards_and_re_evaluates(self):
        tokens = iter(["first", "second"])
        cache = VarCache(
            {"tok": var(on={"get": "{origin}/t"}, json="t", renew=True)},
            ORIGIN,
            lambda request: Document.from_json({"t": next(tokens)}),
        )
        cache.begin_novel("https://e/a")
        assert cache.get("tok") == "first"
        assert cache.renew("tok") == "second"

    def test_a_var_without_renew_refuses_to_be_renewed(self):
        # Retrying on every failure would mask a wrong selector behind repeated requests.
        cache = VarCache(
            {"tok": var(on={"get": "{origin}/t"}, json="t")},
            ORIGIN,
            lambda request: Document.from_json({"t": "x"}),
        )
        cache.begin_novel("https://e/a")
        with pytest.raises(VarError, match="does not declare renew"):
            cache.renew("tok")

    def test_renewing_an_unknown_var_is_reported(self):
        with pytest.raises(VarError, match="unknown var"):
            VarCache({}, ORIGIN).renew("nope")


class TestOwnRequest:
    def test_it_needs_a_fetcher(self):
        cache = VarCache({"tok": var(on={"get": "{origin}/t"}, json="t")}, ORIGIN)
        cache.begin_novel("https://e/a")
        with pytest.raises(VarError, match="no fetcher was given"):
            cache.get("tok")

    def test_the_request_reaches_the_fetcher_intact(self):
        seen = {}

        def fetch(request):
            seen["get"] = request.get
            return Document.from_json({"t": "ok"})

        cache = VarCache({"tok": var(on={"get": "{origin}/api/t"}, json="t")}, ORIGIN, fetch)
        cache.begin_novel("https://e/a")
        cache.get("tok")
        assert isinstance(seen["get"], str) and seen["get"].endswith("/api/t")


class TestLazyMapping:
    def test_an_unused_var_is_never_evaluated(self):
        calls = []

        def fetch(request):
            calls.append(1)
            return Document.from_json({"t": "x"})

        cache = VarCache(
            {
                "used": var(on="url", regex=r"/(\d+)"),
                "unused": var(on={"get": "{origin}/t"}, json="t"),
            },
            ORIGIN,
            fetch,
        )
        cache.begin_novel("https://e/novel/7")
        mapping = cache.as_mapping()
        assert mapping["used"] == "7"
        assert calls == []

    def test_it_reports_what_is_declared(self):
        cache = VarCache({"a": var(on="url", regex="x"), "b": var(on="url", regex="y")}, ORIGIN)
        mapping = cache.as_mapping()
        assert set(mapping) == {"a", "b"}
        assert len(mapping) == 2
        assert "a" in mapping and "z" not in mapping

    def test_it_works_as_a_render_context(self):
        from sourcelib.interpolate import render

        cache = VarCache({"nid": var(on="url", regex=r"/novel/(\d+)")}, ORIGIN)
        cache.begin_novel("https://e.test/novel/42/")
        context = {"origin": ORIGIN, "vars": cache.as_mapping()}
        assert render("{origin}/api/{vars.nid}", context) == "https://e.test/api/42"


class TestRequestScope:
    def test_a_request_backed_var_is_session_scoped(self):
        spec = var(on=Request.model_validate({"get": "{origin}/t"}), json="t")
        cache = VarCache({"t": spec}, ORIGIN, lambda r: Document.from_json({"t": "v"}))
        assert VarCache._scope_of(spec) == "session"
        cache.begin_novel("https://e/a")
        assert cache.get("t") == "v"
