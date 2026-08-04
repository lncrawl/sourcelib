"""Requests and pagination, per RFC-0001 sections 3.6 and 3.7."""

import pytest

from sourcelib.fetch import (
    Fetched,
    FetchError,
    RecordedFetcher,
    encode_payload,
    harvest_form,
    run_request,
    walk_pages,
)
from sourcelib.interpolate import context_for
from sourcelib.spec.extract import Document
from sourcelib.spec.model import Paginate, Request

ORIGIN = "https://example.com"


def request(**fields):
    return Request.model_validate(fields)


def paginate(**fields):
    return Paginate.model_validate(fields)


def rows(count, page=1):
    items = "".join(f'<li><a href="/c/{page}-{i}">Ch {page}.{i}</a></li>' for i in range(count))
    return f"<html><body><ul>{items}</ul></body></html>"


def has_rows(document):
    return bool(document.node and document.node.select("ul li"))


class TestEncodePayload:
    def test_flat_strings_are_form_encoded(self):
        assert encode_payload({"searchkey": "x"}, {})[0] == "form"

    @pytest.mark.parametrize(
        "payload",
        [
            {"retry": False},
            {"page": 2},
            {"score": 1.5},
            {"filters": ["a"]},
            {"nested": {"a": 1}},
        ],
    )
    def test_anything_unform_encodable_becomes_json(self, payload):
        assert encode_payload(payload, {})[0] == "json"

    def test_an_explicit_content_type_wins(self):
        # The one case inference reads wrongly: an API whose body is flat strings.
        headers = {"content-type": "application/json"}
        assert encode_payload({"text": "q"}, headers)[0] == "json"

    def test_the_header_is_matched_case_insensitively(self):
        assert encode_payload({"text": "q"}, {"Content-Type": "application/json"})[0] == "json"

    def test_an_explicit_form_content_type_also_wins(self):
        headers = {"content-type": "application/x-www-form-urlencoded"}
        assert encode_payload({"page": 2}, headers)[0] == "form"


class TestHarvestForm:
    def test_it_reads_hidden_inputs_and_the_action(self):
        markup = """<form action="/do-search" method="post">
          <input name="_token" value="abc123"/>
          <input name="state" value="xyz"/>
          <input name="keyboard" value=""/>
        </form>"""
        document = Document.from_html(markup, url="https://example.com/search.html")
        values, action = harvest_form(document, "form")
        assert values == {"_token": "abc123", "state": "xyz", "keyboard": ""}
        assert action == "https://example.com/do-search"

    def test_a_form_with_no_action_posts_to_itself(self):
        document = Document.from_html('<form><input name="q"/></form>', url="https://a.example/s")
        assert harvest_form(document, "form")[1] == "https://a.example/s"

    def test_selects_and_textareas_are_harvested_too(self):
        markup = '<form><select name="lang"></select><textarea name="body"></textarea></form>'
        values, _ = harvest_form(Document.from_html(markup), "form")
        assert set(values) == {"lang", "body"}

    def test_a_selector_matching_nothing_is_reported(self):
        with pytest.raises(FetchError, match="matched nothing"):
            harvest_form(Document.from_html("<div/>"), "form.search")


class TestRunRequest:
    def test_a_get_renders_its_template(self):
        fetcher = RecordedFetcher({"https://example.com/s?q=a+b": "<html/>"})
        run_request(
            request(get="{origin}/s?q={query|plus}"),
            fetcher,
            context_for(ORIGIN, query="a b"),
        )
        assert fetcher.calls == [("GET", "https://example.com/s?q=a+b")]

    def test_a_post_sends_a_form_body(self):
        fetcher = RecordedFetcher({"https://example.com/api": "<html/>"})
        run_request(
            request(post="{origin}/api", payload={"searchkey": "{query}"}),
            fetcher,
            context_for(ORIGIN, query="thing"),
        )
        assert fetcher.bodies[0]["form"] == {"searchkey": "thing"}
        assert fetcher.bodies[0]["json"] is None

    def test_a_post_sends_json_when_the_shape_demands_it(self):
        fetcher = RecordedFetcher({"https://example.com/api": "<html/>"})
        run_request(
            request(post="{origin}/api", payload={"translate": "web", "retry": False}),
            fetcher,
            context_for(ORIGIN),
        )
        assert fetcher.bodies[0]["json"] == {"translate": "web", "retry": False}

    def test_headers_merge_over_the_spec_and_interpolate(self):
        fetcher = RecordedFetcher({"https://example.com/x": "<html/>"})
        run_request(
            request(get="{origin}/x", headers={"x-token": "{vars.tok}"}),
            fetcher,
            context_for(ORIGIN, {"tok": "abc"}),
            spec_headers={"accept": "text/html", "x-token": "overridden"},
        )
        sent = fetcher.bodies[0]["headers"]
        assert sent == {"accept": "text/html", "x-token": "abc"}

    def test_a_request_encoding_overrides_the_spec_one(self):
        fetcher = RecordedFetcher({"https://example.com/x": "<html/>"})
        run_request(
            request(get="{origin}/x", encoding="gbk"),
            fetcher,
            context_for(ORIGIN),
            spec_encoding="utf-8",
        )
        assert fetcher.bodies[0]["encoding"] == "gbk"

    def test_render_goes_to_the_browser_with_its_selector(self):
        fetcher = RecordedFetcher({"https://example.com/app": "<html/>"})
        run_request(
            request(get="{origin}/app", render=True, wait_for=".ready"),
            fetcher,
            context_for(ORIGIN),
        )
        assert fetcher.calls[0][0] == "RENDER"

    def test_a_request_with_no_address_uses_the_stage_default(self):
        # A chapter that declares only paginate inherits the URL the toc captured.
        fetcher = RecordedFetcher({"https://example.com/c/1": "<html/>"})
        document = run_request(
            request(paginate={"next": {"css": "a.next"}}),
            fetcher,
            context_for(ORIGIN),
            default_url="https://example.com/c/1",
        )
        assert document.url == "https://example.com/c/1"

    def test_no_address_and_no_default_is_an_error(self):
        with pytest.raises(FetchError, match="needs a stage default"):
            run_request(request(headers={"a": "b"}), RecordedFetcher({}), context_for(ORIGIN))


class TestPageReuse:
    def test_page_returns_a_cached_document_without_fetching(self):
        fetcher = RecordedFetcher({})
        novel = Document.from_html("<html/>", url="https://example.com/novel/x")
        document = run_request(
            request(page="novel"), fetcher, context_for(ORIGIN), cache={"novel": novel}
        )
        assert document is novel
        assert fetcher.calls == []

    def test_page_naming_something_not_yet_fetched_is_refused(self):
        # Refusing rather than fetching is what stops a stage doubling a site's traffic.
        with pytest.raises(FetchError, match="has not been fetched yet"):
            run_request(request(page="novel"), RecordedFetcher({}), context_for(ORIGIN), cache={})

    def test_a_named_request_is_cached_for_later_reuse(self):
        fetcher = RecordedFetcher({"https://example.com/a": "<html/>"})
        cache = {}
        run_request(
            request(name="listing", get="{origin}/a"), fetcher, context_for(ORIGIN), cache=cache
        )
        assert "listing" in cache


class TestFromAlternatives:
    def test_the_first_alternative_that_yields_items_wins(self):
        fetcher = RecordedFetcher(
            {
                "https://example.com/ajax": rows(0),
                "https://example.com/admin": rows(3),
            }
        )
        document = run_request(
            request(**{"from": [{"get": "{origin}/ajax"}, {"get": "{origin}/admin"}]}),
            fetcher,
            context_for(ORIGIN),
            yields=has_rows,
        )
        assert has_rows(document)
        assert [url for _, url in fetcher.calls] == [
            "https://example.com/ajax",
            "https://example.com/admin",
        ]

    def test_it_stops_at_the_first_success(self):
        fetcher = RecordedFetcher(
            {"https://example.com/ajax": rows(2), "https://example.com/admin": rows(9)}
        )
        run_request(
            request(**{"from": [{"get": "{origin}/ajax"}, {"get": "{origin}/admin"}]}),
            fetcher,
            context_for(ORIGIN),
            yields=has_rows,
        )
        assert len(fetcher.calls) == 1

    def test_a_failing_alternative_is_skipped(self):
        fetcher = RecordedFetcher({"https://example.com/b": rows(1)})
        document = run_request(
            request(**{"from": [{"get": "{origin}/a"}, {"get": "{origin}/b"}]}),
            fetcher,
            context_for(ORIGIN),
            yields=has_rows,
        )
        assert has_rows(document)

    def test_all_alternatives_failing_is_reported(self):
        with pytest.raises(FetchError, match="no alternative in `from`"):
            run_request(
                request(**{"from": [{"get": "{origin}/a"}, {"get": "{origin}/b"}]}),
                RecordedFetcher({}),
                context_for(ORIGIN),
                yields=has_rows,
            )

    def test_without_a_yields_test_the_first_reachable_one_is_used(self):
        fetcher = RecordedFetcher({"https://example.com/a": rows(0)})
        run_request(request(**{"from": [{"get": "{origin}/a"}]}), fetcher, context_for(ORIGIN))
        assert len(fetcher.calls) == 1


class TestFormTwoStep:
    def test_get_plus_form_posts_to_the_forms_own_action(self):
        page = """<html><body><div class="search-container">
          <form method="post" action="/do"><input name="_token" value="t0k"/></form>
        </div></body></html>"""
        fetcher = RecordedFetcher(
            {"https://example.com/search.html": page, "https://example.com/do": rows(2)}
        )
        run_request(
            request(
                get="{origin}/search.html",
                form=".search-container form",
                payload={"keyboard": "{query}"},
            ),
            fetcher,
            context_for(ORIGIN, query="sword"),
        )
        assert fetcher.calls == [
            ("GET", "https://example.com/search.html"),
            ("POST", "https://example.com/do"),
        ]
        # The harvested token travels with the spec's own value applied over it.
        assert fetcher.bodies[-1]["form"] == {"_token": "t0k", "keyboard": "sword"}

    def test_post_plus_form_reads_the_document_in_scope(self):
        novel = Document.from_html(
            '<form id="f"><input name="nonce" value="n1"/></form>',
            url="https://example.com/novel/x",
        )
        fetcher = RecordedFetcher({"https://example.com/ajax": rows(1)})
        run_request(
            request(post="{origin}/ajax", form="#f", payload={"id": "9"}),
            fetcher,
            context_for(ORIGIN),
            cache={"novel": novel},
        )
        assert fetcher.calls == [("POST", "https://example.com/ajax")]
        assert fetcher.bodies[-1]["form"] == {"nonce": "n1", "id": "9"}

    def test_post_plus_form_without_a_document_in_scope_is_refused(self):
        with pytest.raises(FetchError, match="needs a document already in scope"):
            run_request(
                request(post="{origin}/ajax", form="#f"),
                RecordedFetcher({}),
                context_for(ORIGIN),
                cache={},
            )


class TestPaginateCount:
    def test_it_reads_the_count_and_fetches_the_rest(self):
        first = Document.from_html(
            '<div id="pager"><a>1</a><a>2</a><a>3</a></div>' + rows(2),
            url="https://example.com/list",
        )
        fetcher = RecordedFetcher(
            {
                "https://example.com/list?page=2": rows(2, 2),
                "https://example.com/list?page=3": rows(2, 3),
            }
        )
        pages, truncated = walk_pages(
            first,
            paginate(
                count={"css": "#pager a", "all": True, "pipe": ["max"]},
                url="{origin}/list?page={page}",
            ),
            fetcher,
            context_for(ORIGIN),
        )
        assert len(pages) == 3
        assert truncated is False

    def test_the_first_page_is_never_refetched(self):
        first = Document.from_html('<div id="p"><a>2</a></div>', url="https://example.com/list")
        fetcher = RecordedFetcher({"https://example.com/list?page=2": rows(1, 2)})
        walk_pages(
            first,
            paginate(count={"css": "#p a"}, url="{origin}/list?page={page}"),
            fetcher,
            context_for(ORIGIN),
        )
        # Sites address the first page differently, so generating page=1 would 404 or dupe.
        assert [url for _, url in fetcher.calls] == ["https://example.com/list?page=2"]

    def test_a_single_page_needs_no_extra_request(self):
        first = Document.from_html('<div id="p"><a>1</a></div>', url="https://example.com/list")
        fetcher = RecordedFetcher({})
        pages, _ = walk_pages(
            first,
            paginate(count={"css": "#p a"}, url="{origin}/list?page={page}"),
            fetcher,
            context_for(ORIGIN),
        )
        assert len(pages) == 1 and fetcher.calls == []

    def test_concurrent_pages_are_assembled_by_index_not_completion(self):
        # RFC-0001 section 4.5: assembling by completion would number chapters differently on
        # every run, and the numbering is what the rest of the system stores.
        first = Document.from_html('<div id="p"><a>6</a></div>' + rows(1, 1), url="https://e/list")
        fetcher = RecordedFetcher({f"https://e/list?page={n}": rows(1, n) for n in range(2, 7)})
        pages, _ = walk_pages(
            first,
            paginate(count={"css": "#p a"}, url="https://e/list?page={page}", concurrent=True),
            fetcher,
            context_for(ORIGIN),
            workers=5,
        )
        assert [p.url for p in pages] == ["https://e/list"] + [
            f"https://e/list?page={n}" for n in range(2, 7)
        ]

    def test_a_limit_truncates_and_says_so(self):
        first = Document.from_html('<div id="p"><a>9</a></div>', url="https://e/list")
        fetcher = RecordedFetcher({f"https://e/list?page={n}": rows(1, n) for n in range(2, 10)})
        pages, truncated = walk_pages(
            first,
            paginate(count={"css": "#p a"}, url="https://e/list?page={page}", limit=3),
            fetcher,
            context_for(ORIGIN),
        )
        # A silent cap is indistinguishable from a site with fewer pages.
        assert len(pages) == 3 and truncated is True

    def test_an_unreadable_count_falls_back_to_one_page(self):
        first = Document.from_html("<div/>", url="https://e/list")
        pages, _ = walk_pages(
            first,
            paginate(count={"css": "#absent"}, url="https://e/list?page={page}"),
            RecordedFetcher({}),
            context_for(ORIGIN),
        )
        assert len(pages) == 1

    def test_request_url_is_available_to_the_page_template(self):
        first = Document.from_html('<div id="p"><a>2</a></div>', url="https://e/novel/x/chapters")
        fetcher = RecordedFetcher({"https://e/novel/x/chapters?p=2": rows(1, 2)})
        walk_pages(
            first,
            paginate(count={"css": "#p a"}, url="{request_url}?p={page}"),
            fetcher,
            context_for(ORIGIN),
        )
        assert fetcher.calls[0][1] == "https://e/novel/x/chapters?p=2"


class TestPaginateWhile:
    def test_it_stops_at_the_first_empty_page(self):
        first = Document.from_html(rows(2), url="https://e/list")
        fetcher = RecordedFetcher(
            {
                "https://e/list?page=2": rows(2, 2),
                "https://e/list?page=3": rows(0),
            }
        )
        pages, _ = walk_pages(
            first,
            paginate(**{"while": "has_items", "url": "https://e/list?page={page}"}),
            fetcher,
            context_for(ORIGIN),
            has_items=has_rows,
        )
        assert len(pages) == 2

    def test_it_stops_when_a_page_cannot_be_fetched(self):
        first = Document.from_html(rows(1), url="https://e/list")
        pages, _ = walk_pages(
            first,
            paginate(**{"while": "has_items", "url": "https://e/list?page={page}"}),
            RecordedFetcher({}),
            context_for(ORIGIN),
            has_items=has_rows,
        )
        assert len(pages) == 1

    def test_a_limit_applies(self):
        first = Document.from_html(rows(1), url="https://e/list")
        fetcher = RecordedFetcher({f"https://e/list?page={n}": rows(1, n) for n in range(2, 20)})
        pages, truncated = walk_pages(
            first,
            paginate(**{"while": "has_items", "url": "https://e/list?page={page}", "limit": 4}),
            fetcher,
            context_for(ORIGIN),
            has_items=has_rows,
        )
        assert len(pages) == 4 and truncated is True

    def test_it_needs_a_way_to_tell_whether_a_page_had_rows(self):
        first = Document.from_html(rows(1), url="https://e/list")
        with pytest.raises(FetchError, match="needs a way to tell"):
            walk_pages(
                first,
                paginate(**{"while": "has_items", "url": "https://e/x?p={page}"}),
                RecordedFetcher({}),
                context_for(ORIGIN),
            )


class TestPaginateNext:
    def test_it_follows_the_link_until_there_is_none(self):
        fetcher = RecordedFetcher(
            {
                "https://e/c/1_2.html": '<a class="next" href="/c/1_3.html">next</a>',
                "https://e/c/1_3.html": "<div>last page</div>",
            }
        )
        first = Document.from_html(
            '<a class="next" href="/c/1_2.html">next</a>', url="https://e/c/1_1.html"
        )
        pages, _ = walk_pages(
            first,
            paginate(next={"css": "a.next", "attr": "href"}),
            fetcher,
            context_for(ORIGIN),
        )
        assert [p.url for p in pages] == [
            "https://e/c/1_1.html",
            "https://e/c/1_2.html",
            "https://e/c/1_3.html",
        ]

    def test_a_regex_on_the_next_link_stops_it_leaving_the_chapter(self):
        # The novel543 case: one link serves "next page" and "next chapter", told apart only
        # by URL shape. A non-matching step yields nothing, so pagination simply stops.
        first = Document.from_html(
            '<a class="next" href="/c/2.html">next chapter</a>', url="https://e/c/1_1.html"
        )
        pages, _ = walk_pages(
            first,
            paginate(
                next={
                    "css": "a.next",
                    "attr": "href",
                    "pipe": [{"regex": {"pattern": r".*_\d+\.html$"}}],
                }
            ),
            RecordedFetcher({}),
            context_for(ORIGIN),
        )
        assert len(pages) == 1

    def test_a_link_looping_back_stops_the_walk(self):
        fetcher = RecordedFetcher(
            {"https://e/b": '<a class="next" href="/a">back</a>'},
        )
        first = Document.from_html('<a class="next" href="/b">next</a>', url="https://e/a")
        pages, _ = walk_pages(
            first, paginate(next={"css": "a.next", "attr": "href"}), fetcher, context_for(ORIGIN)
        )
        assert len(pages) == 2

    def test_a_limit_applies(self):
        fetcher = RecordedFetcher(
            {f"https://e/p{n}": f'<a class="next" href="/p{n + 1}">n</a>' for n in range(2, 20)}
        )
        first = Document.from_html('<a class="next" href="/p2">n</a>', url="https://e/p1")
        pages, truncated = walk_pages(
            first,
            paginate(next={"css": "a.next", "attr": "href"}, limit=3),
            fetcher,
            context_for(ORIGIN),
        )
        assert len(pages) == 3 and truncated is True


class TestNoPagination:
    def test_a_stage_without_paginate_has_one_page(self):
        first = Document.from_html(rows(3), url="https://e/x")
        pages, truncated = walk_pages(first, None, RecordedFetcher({}), context_for(ORIGIN))
        assert pages == [first] and truncated is False


class TestAnAlternativeFailingInAnotherWay:
    """`from` exists for an endpoint that may not be present, and a 404 is how a site says so.

    The HTTP layer raises its own exception rather than a FetchError, so catching only the
    latter made the first missing alternative abort the stage. Found by a live Madara host.
    """

    class Rejecting:
        def __init__(self, pages):
            self.pages = dict(pages)
            self.calls = []

        def fetch(self, method, url, **kwargs):
            self.calls.append((method, url))
            if url not in self.pages:
                raise RuntimeError(f"404 Client Error:  for url: {url}")
            return Fetched(url, self.pages[url])

        def render(self, url, *, wait_for=None):  # pragma: no cover - not exercised
            raise NotImplementedError

    def test_the_next_alternative_is_still_tried(self):
        fetcher = self.Rejecting({"https://example.com/admin": rows(3)})
        document = run_request(
            request(**{"from": [{"get": "{origin}/ajax"}, {"get": "{origin}/admin"}]}),
            fetcher,
            context_for(ORIGIN),
            yields=has_rows,
        )
        assert has_rows(document)
        assert len(fetcher.calls) == 2

    def test_the_reason_names_the_exception(self):
        with pytest.raises(FetchError, match="RuntimeError"):
            run_request(
                request(**{"from": [{"get": "{origin}/ajax"}]}),
                self.Rejecting({}),
                context_for(ORIGIN),
                yields=has_rows,
            )
