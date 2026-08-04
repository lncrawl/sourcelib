"""The extractor engine, per RFC-0001 sections 3.4, 4.1, 4.3 and 6.4."""

import pytest

from sourcelib.spec.extract import Document, ExtractError, default_pipe, extract, read_json_path
from sourcelib.spec.model import Extractor

PAGE = """
<html><head>
  <script id="__NEXT_DATA__">{"props":{"novel":{"title":"From JSON","id":77}}}</script>
</head><body>
  <h1 class="title">The Real Title</h1>
  <figure><img data-src="/covers/real.jpg" src="/ph.gif"/></figure>
  <div class="author"><span>Author: Ada</span></div>
  <ul class="tags"><li>#Action</li><li>Drama</li><li>#Action</li><li> </li></ul>
  <div class="summary"><p>First line.</p><p>Second <span>line</span>.</p></div>
  <ul class="chapters">
    <li><a href="/c/1">Chapter 1</a></li>
    <li><a href="/c/2">Chapter 2</a></li>
  </ul>
  <div id="pager"><a>1</a><a>2</a><a>17</a><a>Next</a></div>
</body></html>
"""


@pytest.fixture
def page():
    return Document.from_html(PAGE, url="https://example.com/novel/thing")


def read(page, kind=None, **fields):
    return extract(Extractor.model_validate(fields), page, kind=kind)


class TestReadJsonPath:
    def test_dollar_is_the_whole_body(self):
        assert read_json_path([1, 2], "$") == [1, 2]

    def test_a_dotted_path(self):
        assert read_json_path({"a": {"b": "c"}}, "a.b") == "c"

    def test_a_numeric_segment_indexes_a_list(self):
        assert read_json_path({"rows": [{"id": 7}]}, "rows.0.id") == 7

    def test_a_bare_array_at_the_top_level(self):
        assert read_json_path([{"id": 1}], "0.id") == 1

    def test_a_missing_key_is_none(self):
        assert read_json_path({"a": 1}, "b.c") is None

    def test_an_out_of_range_index_is_none(self):
        assert read_json_path({"rows": []}, "rows.5") is None

    def test_indexing_something_that_is_not_a_list_is_none(self):
        assert read_json_path({"a": "text"}, "a.0") is None

    def test_a_negative_index_reads_from_the_end(self):
        assert read_json_path({"rows": [1, 2, 3]}, "rows.-1") == 3


class TestSources:
    def test_css_with_the_text_default(self, page):
        assert read(page, css="h1.title") == "The Real Title"

    def test_an_attribute(self, page):
        assert read(page, css="figure img", attr="data-src") == "/covers/real.jpg"

    def test_an_attribute_list_is_tried_in_order(self, page):
        # The lazy-image case: try the deferred attribute, then the real one.
        assert read(page, css="figure img", attr=["data-lazy-src", "data-src", "src"]) == (
            "/covers/real.jpg"
        )

    def test_an_attribute_list_falls_through_to_a_present_one(self, page):
        assert read(page, css="figure img", attr=["nope", "src"]) == "/ph.gif"

    def test_html_is_inner_markup(self, page):
        assert read(page, css="div.summary", attr="html").startswith("<p>")

    def test_outer_html_includes_the_element(self, page):
        assert read(page, css="h1.title", attr="outer_html").startswith("<h1")

    def test_css_plus_json_reads_a_data_script(self, page):
        assert read(page, css="script#__NEXT_DATA__", json="props.novel.title") == "From JSON"

    def test_css_plus_regex_reads_inside_the_element(self, page):
        assert read(page, css="div.author", regex=r"Author:\s*(\w+)") == "Ada"

    def test_regex_alone_runs_over_the_raw_document(self, page):
        assert read(page, regex=r'id="__NEXT_DATA__"') == 'id="__NEXT_DATA__"'

    def test_a_const_needs_no_document(self, page):
        assert read(page, const="fixed") == "fixed"

    def test_a_header_is_read_case_insensitively(self):
        document = Document.from_html("<html/>", headers={"X-WP-TotalPages": "12"})
        assert read(document, header="x-wp-totalpages") == "12"

    def test_a_missing_header_is_empty(self):
        document = Document.from_html("<html/>", headers={})
        assert read(document, header="x-absent") in (None, "")

    def test_no_source_reads_the_node_in_scope(self, page):
        row = page.node.select_one("ul.chapters li")
        assert extract(Extractor.model_validate({"css": "a"}), page, scope=row) == "Chapter 1"

    def test_a_selector_matching_nothing_is_empty(self, page):
        assert read(page, css=".absent") in (None, "")

    def test_json_over_a_json_body(self):
        document = Document.from_json({"data": {"title": "API Title"}})
        assert read(document, json="data.title") == "API Title"


class TestAll:
    def test_all_produces_a_list(self, page):
        assert read(page, css="ul.chapters a", all=True) == ["Chapter 1", "Chapter 2"]

    def test_all_maps_the_attribute(self, page):
        assert read(page, css="ul.chapters a", all=True, attr="href") == ["/c/1", "/c/2"]

    def test_all_with_no_matches_is_an_empty_list(self, page):
        assert read(page, css=".absent", all=True) == []

    def test_a_pipe_maps_element_wise_over_all(self, page):
        # Section 4.1: `all` before `pipe` is what makes this work without a `map` construct.
        value = read(page, css="ul.tags li", all=True, pipe=[{"strip_prefix": "#"}, "trim"])
        assert value == ["Action", "Drama", "Action", ""]


class TestEvaluationOrder:
    def test_default_applies_when_everything_else_was_empty(self, page):
        assert read(page, css=".absent", default="fallback value") == "fallback value"

    def test_default_does_not_override_a_real_value(self, page):
        assert read(page, css="h1.title", default="unused") == "The Real Title"

    def test_fallback_is_tried_when_the_result_is_empty(self, page):
        value = read(page, css="h1.new-title", fallback=[{"css": "h1.title"}])
        assert value == "The Real Title"

    def test_fallback_order_is_first_match_wins(self, page):
        value = read(
            page,
            css=".absent",
            fallback=[{"css": ".also-absent"}, {"css": "h1.title"}, {"const": "never"}],
        )
        assert value == "The Real Title"

    def test_a_pipe_emptying_a_value_triggers_fallback(self, page):
        # A filter that rejects everything leaves nothing, so the alternative runs.
        value = read(
            page,
            css="h1.title",
            pipe=[{"regex": {"pattern": "no such text"}}],
            fallback=[{"const": "rescued"}],
        )
        assert value == "rescued"

    def test_default_is_preferred_over_fallback(self, page):
        value = read(page, css=".absent", default="from default", fallback=[{"const": "from fb"}])
        assert value == "from default"


class TestDefaultPipes:
    def test_the_general_default_trims_and_collapses(self):
        assert default_pipe(None, value_is_text=True) == ["trim", "collapse_spaces"]

    def test_tags_deduplicate(self):
        assert default_pipe("tags", value_is_text=True) == [
            "trim",
            "collapse_spaces",
            "drop_empty",
            "unique",
        ]

    def test_a_url_field_only_trims(self):
        assert default_pipe("url", value_is_text=True) == ["trim"]
        assert default_pipe("cover", value_is_text=True) == ["trim"]

    def test_a_body_flattens_inline_wrappers_then_paragraphs(self):
        steps = default_pipe("body", value_is_text=False)
        assert steps[0]["unwrap"][0] == "a"
        assert steps[-1] == "paragraphs"

    def test_a_node_default_over_text_gets_parse_html_first(self):
        # A body arriving inside a JSON field needs no boilerplate in the spec.
        assert default_pipe("body", value_is_text=True)[0] == "parse_html"

    def test_a_node_default_over_a_node_does_not(self):
        assert default_pipe("body", value_is_text=False)[0] != "parse_html"

    def test_tags_get_the_tag_default_applied(self, page):
        # Duplicates removed, blanks dropped, order of first appearance kept.
        assert read(page, kind="tags", css="ul.tags li", all=True) == ["#Action", "Drama"]

    def test_a_declared_pipe_replaces_the_default(self, page):
        value = read(page, kind="tags", css="ul.tags li", all=True, pipe=["trim"])
        assert value == ["#Action", "Drama", "#Action", ""]

    def test_a_synopsis_becomes_paragraphs(self, page):
        value = read(page, kind="synopsis", css="div.summary")
        assert value == "<p>First line.</p><p>Second line.</p>"

    def test_a_body_from_a_json_string_is_parsed_first(self):
        document = Document.from_json({"body": "<p>One</p><p>Two</p>"})
        assert read(document, kind="body", json="body") == "<p>One</p><p>Two</p>"

    def test_an_undeclared_attr_lets_a_node_reach_a_node_pipe(self, page):
        # Section 3.4. With attr defaulting to text the element would be flattened first and
        # every paragraph boundary lost, which makes a chapter body inexpressible.
        value = read(page, css="div.summary", pipe=[{"paragraphs": {"block_tags": ["p"]}}])
        # The span survives because this pipe declares no unwrap; what matters is that the
        # paragraph boundary did too.
        assert value == "<p>First line.</p><p>Second <span>line</span>.</p>"

    def test_an_explicit_attr_text_still_wins(self, page):
        # Asking for text and then running a node step is a type error, reported under 6.1.
        from sourcelib.transform import StepError

        with pytest.raises(StepError, match="expected a node"):
            read(page, css="div.summary", attr="text", pipe=["paragraphs"])

    def test_a_text_pipe_still_gets_text(self, page):
        assert read(page, css="h1.title", pipe=["trim", "lower"]) == "the real title"

    def test_a_named_node_pipe_also_keeps_the_node(self, page):
        value = extract(
            Extractor.model_validate({"css": "div.summary", "pipe": "clean"}),
            page,
            pipes={"clean": [{"paragraphs": {"block_tags": ["p"]}}]},
        )
        assert value == "<p>First line.</p><p>Second <span>line</span>.</p>"


class TestRelativeUrls:
    def test_a_url_field_resolves_against_the_document(self, page):
        assert read(page, kind="url", css="ul.chapters a", attr="href") == "https://example.com/c/1"

    def test_a_cover_resolves_too(self, page):
        assert read(page, kind="cover", css="figure img", attr="data-src") == (
            "https://example.com/covers/real.jpg"
        )

    def test_a_list_of_urls_all_resolve(self, page):
        value = read(page, kind="url", css="ul.chapters a", all=True, attr="href")
        assert value == ["https://example.com/c/1", "https://example.com/c/2"]

    def test_it_resolves_against_the_document_not_the_origin(self):
        # A paginated stage may have walked into a subdirectory; resolving against base_url
        # would produce the wrong link.
        document = Document.from_html(
            '<a href="ch-1">x</a>', url="https://example.com/novel/thing/chapters/page-2"
        )
        assert read(document, kind="url", css="a", attr="href") == (
            "https://example.com/novel/thing/chapters/ch-1"
        )

    def test_an_absolute_url_is_left_alone(self):
        document = Document.from_html(
            '<a href="https://cdn.other/x">y</a>', url="https://a.example/"
        )
        assert read(document, kind="url", css="a", attr="href") == "https://cdn.other/x"

    def test_a_non_url_field_is_not_resolved(self, page):
        assert read(page, css="ul.chapters a", attr="href") == "/c/1"


class TestPipesByName:
    def test_a_named_pipe_is_expanded(self, page):
        value = extract(
            Extractor.model_validate({"css": "h1.title", "pipe": "shout"}),
            page,
            pipes={"shout": ["trim", "lower"]},
        )
        assert value == "the real title"


class TestPagerCount:
    def test_the_page_count_comes_from_max_over_the_pager(self, page):
        # The recurring job `max` exists for: a pager whose last link is a label.
        value = read(page, css="#pager a", all=True, pipe=["max"])
        assert value == "17"


class TestErrors:
    def test_a_template_string_is_not_extractable(self, page):
        with pytest.raises(ExtractError, match="rendered, not extracted"):
            extract("{origin}/x", page)

    def test_a_css_selector_without_a_document_is_an_error(self):
        with pytest.raises(ExtractError, match="needs a parsed document"):
            read(Document(url="https://example.com/"), css="h1")
