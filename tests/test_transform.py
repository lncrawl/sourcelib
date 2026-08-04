"""The transform registry, per RFC-0001 section 6."""

import pytest
from bs4 import BeautifulSoup

from sourcelib.spec.extract import DEFAULT_PARSER
from sourcelib.transform import (
    REGISTRY,
    StepError,
    apply_pipe,
    apply_step,
    parse_step,
    validate_pipe,
)


def soup(markup):
    return BeautifulSoup(markup, DEFAULT_PARSER)


class TestParseStep:
    def test_a_bare_name(self):
        assert parse_step("trim") == ("trim", None)

    def test_one_argument(self):
        assert parse_step({"strip_prefix": "Author:"}) == ("strip_prefix", "Author:")

    def test_named_arguments(self):
        assert parse_step({"regex": {"pattern": r"\d+"}}) == ("regex", {"pattern": r"\d+"})

    @pytest.mark.parametrize("bad", [{"a": 1, "b": 2}, 42, None, ["trim"]])
    def test_anything_else_is_refused(self, bad):
        with pytest.raises(StepError):
            parse_step(bad)


class TestFiltersVersusCleanups:
    """Section 6.2. Getting this backwards deletes rows while the crawl still succeeds."""

    def test_regex_without_a_match_yields_nothing(self):
        assert apply_step("chapter 5", {"regex": {"pattern": r"volume (\d+)"}}) == ""

    def test_reject_yields_nothing_when_it_matches(self):
        assert apply_step("Table of Contents", {"reject": "Contents"}) == ""
        assert apply_step("Chapter 1", {"reject": "Contents"}) == "Chapter 1"

    def test_strip_prefix_passes_an_absent_prefix_through(self):
        # The konkon case: tag pills variably carry a '#', and emptying the ones without it
        # would delete them silently.
        assert apply_step("#action", {"strip_prefix": "#"}) == "action"
        assert apply_step("romance", {"strip_prefix": "#"}) == "romance"

    def test_strip_suffix_passes_an_absent_suffix_through(self):
        assert apply_step("Title - Read", {"strip_suffix": " - Read"}) == "Title"
        assert apply_step("Title", {"strip_suffix": " - Read"}) == "Title"

    def test_replace_without_a_match_passes_through(self):
        assert apply_step("plain", {"replace": {"pattern": "<b>", "with": ""}}) == "plain"

    def test_split_without_the_separator_yields_one_entry(self):
        # A single author with no comma must survive.
        assert apply_step("Solo Author", {"split": ","}) == ["Solo Author"]
        assert apply_step("A, B", {"split": ","}) == ["A", " B"]

    def test_a_cleanup_over_a_list_keeps_every_entry(self):
        tags = ["#a", "b", "#c"]
        assert apply_step(tags, {"strip_prefix": "#"}) == ["a", "b", "c"]


class TestMappingOverLists:
    """Section 6.3: a scalar step over a list runs element-wise, so there is no `map`."""

    def test_a_text_step_maps(self):
        assert apply_step([" a ", " b "], "trim") == ["a", "b"]

    def test_a_list_step_does_not_map(self):
        assert apply_step(["a", "", "b"], "drop_empty") == ["a", "b"]

    def test_a_node_step_maps(self):
        nodes = [soup("<div><i>x</i></div>"), soup("<div><i>y</i></div>")]
        result = apply_step(nodes, {"strip_tags": "i"})
        assert all(r.find("i") is None for r in result)

    def test_a_scalar_over_a_single_value_is_unchanged(self):
        assert apply_step(" a ", "trim") == "a"


class TestTextSteps:
    def test_trim_and_collapse_spaces(self):
        assert apply_step("  a\n\tb  ", "collapse_spaces") == "a b"

    def test_collapse_spaces_folds_no_break_space(self):
        assert apply_step("a  b", "collapse_spaces") == "a b"

    def test_title_case_preserves_the_rest_of_each_word(self):
        # str.title() would give "Abc Don'T Xml" and destroy both.
        assert apply_step("aBC don't XML", "title_case") == "ABC Don't XML"

    def test_lower(self):
        assert apply_step("ABC", "lower") == "abc"

    @pytest.mark.parametrize("form", ["NFC", "NFD", "NFKC", "NFKD"])
    def test_normalize_unicode_accepts_each_form(self, form):
        assert apply_step("ﬁx", {"normalize_unicode": {"form": form}})

    def test_normalize_unicode_defaults_to_nfkc(self):
        assert apply_step("ﬁx", "normalize_unicode") == "fix"

    def test_an_unknown_form_is_refused(self):
        with pytest.raises(StepError, match="NFC, NFD, NFKC or NFKD"):
            apply_step("x", {"normalize_unicode": {"form": "NFKX"}})

    def test_regex_yields_group_one_by_default(self):
        assert apply_step("id=1234x", {"regex": {"pattern": r"id=(\d+)"}}) == "1234"

    def test_regex_without_a_group_yields_the_whole_match(self):
        assert apply_step("id=1234x", {"regex": {"pattern": r"\d+"}}) == "1234"

    def test_regex_can_ask_for_a_later_group(self):
        step = {"regex": {"pattern": r"(\d+)-(\d+)", "group": 2}}
        assert apply_step("10-20", step) == "20"

    def test_asking_for_a_group_that_does_not_exist_is_an_error(self):
        with pytest.raises(StepError, match="asked for 3"):
            apply_step("10-20", {"regex": {"pattern": r"(\d+)-(\d+)", "group": 3}})

    def test_a_pattern_is_required(self):
        with pytest.raises(StepError, match="needs a `pattern`"):
            apply_step("x", {"regex": {}})


class TestListSteps:
    def test_drop_empty_removes_whitespace_only_entries(self):
        assert apply_step(["a", "", "  ", "b", None], "drop_empty") == ["a", "b"]

    def test_unique_preserves_first_appearance_order(self):
        assert apply_step(["b", "a", "b", "c", "a"], "unique") == ["b", "a", "c"]

    def test_join(self):
        assert apply_step(["a", "b"], {"join": " | "}) == "a | b"

    def test_max_reads_the_highest_number_and_skips_labels(self):
        # A pager often ends with "Next", which is why max beats taking the last link.
        assert apply_step(["1", "2", "10", "Next"], "max") == "10"

    def test_max_with_no_numbers_yields_nothing(self):
        assert apply_step(["Next", "Last"], "max") == ""

    def test_max_handles_numbers_inside_text(self):
        assert apply_step(["page 2", "page 30"], "max") == "30"

    def test_lines_to_html_wraps_each_entry(self):
        assert apply_step(["one", "two"], "lines_to_html") == "<p>one</p><p>two</p>"

    def test_lines_to_html_can_write_an_attribute(self):
        step = {"lines_to_html": {"tag": "img", "attr": "src"}}
        assert apply_step(["a.jpg", "b.jpg"], step) == '<img src="a.jpg"/><img src="b.jpg"/>'

    def test_lines_to_html_skips_blank_entries(self):
        assert apply_step(["a", "  ", "b"], "lines_to_html") == "<p>a</p><p>b</p>"


class TestNodeSteps:
    def test_strip_tags_removes_content_too(self):
        node = apply_step(soup("<div>keep<script>bad()</script></div>"), {"strip_tags": "script"})
        assert "bad" not in node.get_text()

    def test_unwrap_keeps_content(self):
        node = apply_step(soup("<div><span>keep</span></div>"), {"unwrap": "span"})
        assert node.find("span") is None
        assert "keep" in node.get_text()

    def test_strip_css_takes_a_list_of_selectors(self):
        markup = '<div><p>text</p><div class="ads">x</div><div class="promo">y</div></div>'
        node = apply_step(soup(markup), {"strip_css": [".ads", ".promo"]})
        assert node.get_text().strip() == "text"

    def test_unwrap_all_leaves_text_only(self):
        node = apply_step(soup("<div><b>a</b><i>b</i></div>"), "unwrap_all")
        assert node.find(True) is None or node.find("b") is None

    def test_keep_attrs_strips_everything_else(self):
        node = apply_step(soup('<img src="a" onclick="x" class="y"/>'), {"keep_attrs": "src"})
        image = node.find("img")
        assert image is not None and set(image.attrs) == {"src"}

    def test_unlazy_images_prefers_the_deferred_attribute(self):
        markup = '<img src="placeholder.gif" data-src="real.jpg"/>'
        node = apply_step(soup(markup), "unlazy_images")
        image = node.find("img")
        assert image is not None and image["src"] == "real.jpg"
        assert "data-src" not in image.attrs

    def test_unlazy_images_tries_lazy_src_first(self):
        markup = '<img src="p.gif" data-src="b.jpg" data-lazy-src="a.jpg"/>'
        image = apply_step(soup(markup), "unlazy_images").find("img")
        assert image is not None and image["src"] == "a.jpg"

    def test_unlazy_images_leaves_a_plain_image_alone(self):
        image = apply_step(soup('<img src="real.jpg"/>'), "unlazy_images").find("img")
        assert image is not None and image["src"] == "real.jpg"

    def test_drop_empty_nodes_keeps_images(self):
        node = apply_step(soup('<div><p></p><p><img src="a"/></p></div>'), "drop_empty_nodes")
        assert node.find("img") is not None
        assert len(node.find_all("p")) == 1

    def test_drop_leading_removes_a_duplicated_heading(self):
        markup = "<div><p>Chapter 5</p><p>Real text</p></div>"
        node = apply_step(soup(markup), {"drop_leading": {"matches": r"(?i)^\s*chapter\s+\d+"}})
        assert "Chapter 5" not in node.get_text()
        assert "Real text" in node.get_text()

    def test_drop_leading_only_looks_within_its_window(self):
        markup = "<div><p>x</p><p>y</p><p>Chapter 5</p></div>"
        node = apply_step(soup(markup), {"drop_leading": {"matches": r"Chapter", "within": 2}})
        assert "Chapter 5" in node.get_text()

    def test_drop_leading_removes_at_most_one(self):
        markup = "<div><p>Chapter 1</p><p>Chapter 2</p></div>"
        step = {"drop_leading": {"matches": "Chapter", "within": 5}}
        node = apply_step(soup(markup), step)
        assert "Chapter 2" in node.get_text()

    def test_drop_leading_needs_a_pattern(self):
        with pytest.raises(StepError, match="needs a `matches`"):
            apply_step(soup("<div/>"), "drop_leading")

    def test_a_node_step_on_text_is_an_error(self):
        with pytest.raises(StepError, match="expected a node"):
            apply_step("not a node", "unwrap_all")


class TestParagraphs:
    def test_block_tags_become_paragraphs(self):
        assert apply_step(soup("<div><div>a</div><div>b</div></div>"), "paragraphs") == (
            "<p>a</p><p>b</p>"
        )

    def test_br_ends_a_paragraph(self):
        assert apply_step(soup("<div>a<br/>b</div>"), "paragraphs") == "<p>a</p><p>b</p>"

    def test_inline_formatting_survives(self):
        result = apply_step(soup("<div><p>a <b>bold</b> c</p></div>"), "paragraphs")
        assert "<b>bold</b>" in result

    def test_comments_scripts_and_styles_are_discarded(self):
        markup = "<div><p>keep<!--gone--><script>x()</script><style>y</style></p></div>"
        result = apply_step(soup(markup), "paragraphs")
        assert result == "<p>keep</p>"

    def test_preserved_elements_are_emitted_whole(self):
        result = apply_step(soup('<div><p><img src="a.jpg"/></p></div>'), "paragraphs")
        assert 'src="a.jpg"' in result

    def test_a_paragraph_with_neither_text_nor_image_is_dropped(self):
        assert apply_step(soup("<div><p></p><p>real</p></div>"), "paragraphs") == "<p>real</p>"

    def test_block_tags_can_be_overridden(self):
        markup = "<div><section>a</section></div>"
        step = {"paragraphs": {"block_tags": ["p"]}}
        assert "<section>" in apply_step(soup(markup), step)

    def test_an_image_only_paragraph_survives(self):
        result = apply_step(soup('<div><p><img src="a"/></p></div>'), "paragraphs")
        assert result.startswith("<p>")


class TestParseHtmlAndText:
    def test_parse_html_turns_a_json_fragment_into_a_document(self):
        node = apply_step("<li><a href='/x'>t</a></li>", "parse_html")
        assert node.select_one("a")["href"] == "/x"

    def test_text_reads_a_node(self):
        assert apply_step(soup("<div>a<b>b</b></div>"), "text") == "ab"

    def test_inner_html_excludes_the_node_itself(self):
        node = soup("<div><p>a</p></div>").find("div")
        assert apply_step(node, "inner_html") == "<p>a</p>"


class TestValidatePipe:
    def test_a_connecting_pipe_reports_what_it_produces(self):
        assert validate_pipe(["text", "trim", "collapse_spaces"], takes="node") == "text"

    def test_a_node_pipe_producing_html(self):
        assert validate_pipe([{"strip_tags": "script"}, "paragraphs"], takes="node") == "html"

    def test_a_type_mismatch_is_refused_before_a_crawl(self):
        with pytest.raises(StepError, match="consumes node but the pipe produced text"):
            validate_pipe(["trim", "unwrap_all"], takes="text")

    def test_an_unknown_step_is_refused(self):
        with pytest.raises(StepError, match="unknown step 'trimm'"):
            validate_pipe(["trimm"])

    def test_html_connects_to_a_text_step(self):
        assert validate_pipe(["paragraphs", "trim"], takes="node") == "text"

    def test_a_list_connects_to_a_scalar_step_and_stays_a_list(self):
        assert validate_pipe([{"split": ","}, "trim"], takes="text") == "list"

    def test_a_list_step_after_a_split(self):
        assert validate_pipe([{"split": ","}, "drop_empty", {"join": "-"}], takes="text") == "text"

    def test_parse_html_reopens_a_node_pipe(self):
        assert validate_pipe(["parse_html", "paragraphs"], takes="text") == "html"

    def test_a_hook_step_connects_to_anything(self):
        assert validate_pipe([{"hook": "hooks/lib/x.py"}, "trim"], takes="node") == "text"

    def test_every_registered_step_declares_its_types(self):
        for name, spec in REGISTRY.items():
            assert spec.takes in ("node", "html", "text", "list", "any"), name
            assert spec.gives in ("node", "html", "text", "list", "any"), name


class TestApplyPipe:
    def test_steps_run_in_order(self):
        pipe = [{"strip_prefix": "Author:"}, "trim", {"split": ","}, "drop_empty"]
        assert apply_pipe("Author: A, B", pipe) == ["A", " B"]

    def test_a_named_pipe_expands(self):
        pipes = {"clean": ["trim", "collapse_spaces"]}
        assert apply_pipe("  a  b  ", ["clean"], pipes) == "a b"

    def test_a_name_that_is_neither_a_step_nor_a_pipe_is_refused(self):
        with pytest.raises(StepError, match="unknown step 'clean'"):
            apply_pipe("x", ["clean"])

    def test_a_named_pipe_may_reference_another(self):
        pipes = {"inner": ["trim"], "outer": ["inner", "lower"]}
        assert apply_pipe("  AB  ", ["outer"], pipes) == "ab"

    def test_a_self_referential_named_pipe_is_refused(self):
        with pytest.raises(StepError, match="refers to itself"):
            apply_pipe("x", ["loop"], {"loop": ["loop"]})

    def test_an_empty_pipe_is_the_identity(self):
        assert apply_pipe("x", []) == "x"

    def test_the_body_default_flattens_inline_wrappers(self):
        # The default chapter.body pipe from section 6.4.
        markup = '<div><p>Read <a href="/x">here</a> now</p><span>plain</span></div>'
        pipe = [{"unwrap": ["a", "abbr", "acronym", "label", "span", "time"]}, "paragraphs"]
        result = apply_pipe(soup(markup), pipe)
        assert "<a " not in result and "<span>" not in result
        assert "Read here now" in result


class TestBlocksAreParserIndependent:
    """A step reading top-level blocks must see the same ones under either parser.

    `lxml` wraps a fragment in `<html><body>` and `html.parser` adds nothing, so code that stepped
    down one level was right for one and left every block buried under `<body>` for the other. That
    failed quietly: the blocks came back as a single element whose text was the whole body, so
    `drop_leading` matched nothing and the duplicated heading stayed in every chapter.
    """

    HEADING = "<p>Chapter 5 The Gate</p><p>Real text.</p>"
    WRAPPED = f"<div>{HEADING}</div>"
    STEP = {"drop_leading": {"matches": r"(?i)^\s*chapter\s+\d+"}}

    @pytest.mark.parametrize("parser", ["lxml", "html.parser"])
    @pytest.mark.parametrize("markup", [HEADING, WRAPPED], ids=["bare", "wrapped"])
    def test_drop_leading_finds_the_heading(self, parser, markup):
        node = apply_step(BeautifulSoup(markup, parser), self.STEP)
        text = node.get_text()
        assert "Chapter 5" not in text
        assert "Real text." in text

    @pytest.mark.parametrize("parser", ["lxml", "html.parser"])
    def test_paragraphs_agree_across_parsers(self, parser):
        assert apply_step(BeautifulSoup(self.WRAPPED, parser), "paragraphs") == (
            "<p>Chapter 5 The Gate</p><p>Real text.</p>"
        )

    def test_a_parsed_fragment_does_not_carry_its_wrapper_into_output(self):
        """The case that shipped silently: a synopsis arriving as a JSON string.

        `parse_html` hands `paragraphs` a document, and `lxml` wraps every fragment in
        `<html><body>`. Treating those as content produced
        `<p><html><body>Pledge for more releases!</body></html></p>` in a real recording.
        """
        node = apply_pipe("Pledge for more releases!", ["parse_html", "paragraphs"], {})
        assert node == "<p>Pledge for more releases!</p>"

    def test_inner_html_of_a_parsed_fragment_is_the_markup_that_went_in(self):
        node = apply_pipe("<p>a</p><p>b</p>", ["parse_html", "inner_html"], {})
        assert node == "<p>a</p><p>b</p>"
