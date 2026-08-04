"""Fetch and extract together, on the shapes that break most often in practice.

These are the cases CONTRIBUTING warns about: a chapter list that swallowed a navigation menu,
one that read only page one, and a body split across pages. Each looks like success.
"""

import pytest

from sourcelib.fetch import RecordedFetcher, run_request, walk_pages
from sourcelib.interpolate import context_for
from sourcelib.spec.extract import extract
from sourcelib.spec.model import Extractor, Paginate, Request

ORIGIN = "https://novelfire.test"


def chapter_rows(page, count=3):
    items = "".join(
        f'<li class="chapter-item"><a href="/book/x/chapter-{page}{i}">Ch {page}.{i}</a></li>'
        for i in range(count)
    )
    return f"""<html><body>
      <nav><a href="/latest">Latest</a><a href="/popular">Popular</a></nav>
      <ul class="chapter-list">{items}</ul>
      <div class="pager"><a>1</a><a>2</a><a>3</a><a>Next</a></div>
    </body></html>"""


def rows_of(document, selector=".chapter-list li"):
    return document.node.select(selector) if document.node else []


class TestPaginatedToc:
    """A toc whose count comes off the pager, which is 22 sources in the corpus."""

    @pytest.fixture
    def fetcher(self):
        return RecordedFetcher(
            {
                f"{ORIGIN}/book/x/chapters": chapter_rows(1),
                f"{ORIGIN}/book/x/chapters?page=2": chapter_rows(2),
                f"{ORIGIN}/book/x/chapters?page=3": chapter_rows(3),
            }
        )

    def test_every_page_is_walked_and_rows_stay_in_order(self, fetcher):
        context = context_for(ORIGIN, novel_url=f"{ORIGIN}/book/x")
        first = run_request(
            Request.model_validate({"get": "{novel_url}/chapters"}), fetcher, context
        )
        pages, truncated = walk_pages(
            first,
            Paginate.model_validate(
                {
                    "count": {"css": ".pager a", "all": True, "pipe": ["max"]},
                    "url": "{novel_url}/chapters?page={page}",
                    "concurrent": True,
                }
            ),
            fetcher,
            context,
        )
        assert len(pages) == 3 and truncated is False

        titles = []
        for page in pages:
            for row in rows_of(page):
                titles.append(extract(Extractor.model_validate({"css": "a"}), page, scope=row))
        assert titles == [f"Ch {p}.{i}" for p in (1, 2, 3) for i in range(3)]

    def test_the_navigation_menu_is_not_harvested(self, fetcher):
        # The most common defect in the corpus: a plausible count that includes a site menu.
        context = context_for(ORIGIN, novel_url=f"{ORIGIN}/book/x")
        first = run_request(
            Request.model_validate({"get": "{novel_url}/chapters"}), fetcher, context
        )
        assert first.node is not None
        loose = first.node.select('a[href^="/"]')
        narrowed = [
            url
            for row in loose
            if (
                url := extract(
                    Extractor.model_validate(
                        {
                            "attr": "href",
                            "pipe": [{"regex": {"pattern": r".*/chapter-\d+$"}}],
                        }
                    ),
                    first,
                    scope=row,
                    kind="url",
                )
            )
        ]
        assert len(loose) == 5  # two menu links plus three chapters
        assert len(narrowed) == 3
        assert all("/chapter-" in u for u in narrowed)


class TestMultiPageChapterBody:
    """A body split across pages, walked by its next link and joined in order."""

    def test_the_parts_are_concatenated_in_order(self):
        fetcher = RecordedFetcher(
            {
                f"{ORIGIN}/c/1_2.html": (
                    '<div class="content"><p>Middle.</p></div>'
                    '<a class="next" href="/c/1_3.html">next</a>'
                ),
                f"{ORIGIN}/c/1_3.html": '<div class="content"><p>End.</p></div>'
                '<a class="next" href="/c/2.html">next chapter</a>',
            }
        )
        first = run_request(
            Request.model_validate({"get": f"{ORIGIN}/c/1_1.html"}),
            RecordedFetcher(
                {
                    f"{ORIGIN}/c/1_1.html": '<div class="content"><p>Start.</p></div>'
                    '<a class="next" href="/c/1_2.html">next</a>'
                }
            ),
            context_for(ORIGIN),
        )

        pages, _ = walk_pages(
            first,
            Paginate.model_validate(
                {
                    "next": {
                        "css": "a.next",
                        "attr": "href",
                        # The site reuses one link for both purposes; only the page-shaped
                        # form should be followed.
                        "pipe": [{"regex": {"pattern": r".*_\d+\.html$"}}],
                    }
                }
            ),
            fetcher,
            context_for(ORIGIN),
        )

        body = "".join(
            extract(Extractor.model_validate({"css": "div.content"}), page, kind="body")
            for page in pages
        )
        assert body == "<p>Start.</p><p>Middle.</p><p>End.</p>"
        # It stopped at the next *chapter* rather than walking into it.
        assert len(pages) == 3


class TestApiSource:
    """A search endpoint whose JSON field holds markup, which is 31 sources in the corpus."""

    def test_json_plus_css_parses_then_selects(self):
        payload = (
            '{"resultview": "<li class=\\"novel-item\\">'
            '<a href=\\"/novel/one\\" title=\\"One\\">One</a></li>'
            '<li class=\\"novel-item\\"><a href=\\"/novel/two\\" title=\\"Two\\">Two</a></li>"}'
        )
        fetcher = RecordedFetcher({f"{ORIGIN}/lnsearchlive": payload})
        document = run_request(
            Request.model_validate(
                {"post": "{origin}/lnsearchlive", "payload": {"inputContent": "{query}"}}
            ),
            fetcher,
            context_for(ORIGIN, query="one"),
        )
        # json reads the string, css parses it and selects rows: section 3.8's implied parse.
        inner = extract(Extractor.model_validate({"json": "resultview"}), document)
        from sourcelib.transform import apply_step

        parsed = apply_step(inner, "parse_html")
        titles = [
            extract(Extractor.model_validate({"css": "a", "attr": "title"}), document, scope=row)
            for row in parsed.select("li.novel-item")
        ]
        assert titles == ["One", "Two"]


class TestWhileHasItems:
    def test_it_stops_on_the_first_empty_page(self):
        fetcher = RecordedFetcher(
            {
                f"{ORIGIN}/list?page=2": chapter_rows(2),
                f"{ORIGIN}/list?page=3": chapter_rows(3, count=0),
            }
        )
        first = run_request(
            Request.model_validate({"get": f"{ORIGIN}/list"}),
            RecordedFetcher({f"{ORIGIN}/list": chapter_rows(1)}),
            context_for(ORIGIN),
        )
        pages, _ = walk_pages(
            first,
            Paginate.model_validate({"while": "has_items", "url": f"{ORIGIN}/list?page={{page}}"}),
            fetcher,
            context_for(ORIGIN),
            has_items=lambda d: bool(rows_of(d)),
        )
        assert len(pages) == 2
