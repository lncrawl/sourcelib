"""Template interpolation, per RFC-0001 section 4.2."""

import pytest

from sourcelib.interpolate import (
    FILTERS,
    PLACEHOLDER_ROOTS,
    TemplateError,
    allowed_roots,
    apply_filter,
    context_for,
    placeholders_in,
    render,
    render_url,
    validate_template,
)

ORIGIN = "https://example.com"


class TestParsing:
    def test_a_plain_string_has_no_placeholders(self):
        assert placeholders_in("https://example.com/search") == []

    def test_a_placeholder_without_filters(self):
        assert placeholders_in("{query}") == [("query", [])]

    def test_filters_compose_left_to_right(self):
        assert placeholders_in("{query|lower|plus}") == [("query", ["lower", "plus"])]

    def test_several_placeholders(self):
        found = placeholders_in("{origin}/s?q={query}&p={page}")
        assert [p for p, _ in found] == ["origin", "query", "page"]

    def test_whitespace_around_filters_is_ignored(self):
        assert placeholders_in("{ query | lower }") == [("query", ["lower"])]


class TestFilters:
    def test_plus_only_touches_spaces(self):
        # Deliberately not an encoder: nothing else is escaped.
        assert apply_filter("plus", "a b&c") == "a+b&c"

    def test_urlencode_uses_percent_twenty(self):
        assert apply_filter("urlencode", "a b/c") == "a%20b%2Fc"

    def test_urlencode_plus_uses_a_plus(self):
        assert apply_filter("urlencode_plus", "a b/c") == "a+b%2Fc"

    def test_the_three_encodings_differ(self):
        # Collapsing them would send the wrong query to a third of a template family.
        results = {apply_filter(name, "a b") for name in ("plus", "urlencode", "urlencode_plus")}
        assert results == {"a+b", "a%20b"}

    def test_lower(self):
        assert apply_filter("lower", "AbC") == "abc"

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("The Great Novel", "the-great-novel"),
            ("a  b", "a-b"),
            ("--edges--", "edges"),
            ("Don't Stop!", "don-t-stop"),
        ],
    )
    def test_slug(self, given, expected):
        assert apply_filter("slug", given) == expected

    def test_an_unknown_filter_is_refused(self):
        with pytest.raises(TemplateError, match="unknown filter 'shout'"):
            apply_filter("shout", "x")


class TestScope:
    def test_origin_and_vars_are_available_everywhere(self):
        for stage in ("search", "novel", "toc", "chapter", "login", "var"):
            assert {"origin", "vars"} <= allowed_roots(stage)

    def test_query_belongs_to_search(self):
        assert "query" in allowed_roots("search")
        assert "query" not in allowed_roots("toc")

    def test_chapter_belongs_to_the_chapter_stage(self):
        assert "chapter" in allowed_roots("chapter")
        assert "chapter" not in allowed_roots("novel")

    def test_novel_url_reaches_three_stages(self):
        assert all("novel_url" in allowed_roots(s) for s in ("novel", "toc", "chapter"))
        assert "novel_url" not in allowed_roots("search")

    def test_page_and_request_url_need_pagination(self):
        assert "page" not in allowed_roots("toc")
        assert {"page", "request_url"} <= allowed_roots("toc", in_paginate=True)

    def test_item_needs_an_item_list(self):
        assert "item" not in allowed_roots("toc")
        assert "item" in allowed_roots("toc", in_item=True)

    def test_credentials_belong_to_login_only(self):
        assert {"username", "password"} <= allowed_roots("login")
        assert "password" not in allowed_roots("chapter")

    def test_a_var_request_gets_only_the_session_scoped_roots(self):
        # It outlives every per-novel and per-chapter value.
        assert allowed_roots("var") == {"origin", "vars"}

    def test_an_unknown_stage_is_refused(self):
        with pytest.raises(TemplateError, match="unknown stage"):
            allowed_roots("prologue")


class TestValidate:
    def test_a_legal_template_passes(self):
        validate_template("{origin}/search?q={query|plus}", allowed_roots("search"))

    def test_an_unknown_placeholder_is_refused(self):
        with pytest.raises(TemplateError, match="unknown placeholder"):
            validate_template("{novel_id}", allowed_roots("novel"))

    def test_a_placeholder_out_of_scope_names_where_it_belongs(self):
        with pytest.raises(TemplateError, match="belongs to the search stage"):
            validate_template("{query}", allowed_roots("chapter"))

    def test_an_unknown_filter_is_refused(self):
        with pytest.raises(TemplateError, match="unknown filter"):
            validate_template("{query|shout}", allowed_roots("search"))

    def test_an_empty_placeholder_is_refused(self):
        with pytest.raises(TemplateError, match="empty placeholder"):
            validate_template("{}", allowed_roots("novel"))

    @pytest.mark.parametrize("root", ["vars", "chapter", "item"])
    def test_a_dotted_root_needs_a_name(self, root):
        roots = allowed_roots("chapter", in_item=True)
        with pytest.raises(TemplateError, match="needs a name"):
            validate_template("{%s}" % root, roots)

    def test_every_documented_root_is_reachable_from_some_scope(self):
        reachable = set()
        for stage in ("search", "novel", "toc", "chapter", "login", "var"):
            reachable |= allowed_roots(stage, in_paginate=True, in_item=True)
        assert reachable == set(PLACEHOLDER_ROOTS)


class TestRender:
    def test_it_substitutes_from_the_context(self):
        context = context_for(ORIGIN, query="the great novel")
        assert render("{origin}/s?q={query|plus}", context) == (
            "https://example.com/s?q=the+great+novel"
        )

    def test_vars_are_read_by_name(self):
        context = context_for(ORIGIN, {"novel_id": "1234"})
        assert render("{origin}/api/{vars.novel_id}", context) == "https://example.com/api/1234"

    def test_item_fields_are_read_by_name(self):
        context = context_for(ORIGIN, item={"serie_id": "7", "slug": "abc"})
        assert render("{origin}/serie-{item.serie_id}/f{item.slug}", context) == (
            "https://example.com/serie-7/fabc"
        )

    def test_chapter_extras_carry_from_the_table_of_contents(self):
        context = context_for(ORIGIN, chapter={"cid": "99"})
        assert render("{origin}/c/{chapter.cid}", context) == "https://example.com/c/99"

    def test_a_page_number_renders(self):
        context = context_for(ORIGIN, page=3)
        assert render("{origin}/list?page={page}", context) == "https://example.com/list?page=3"

    def test_filters_compose_in_order(self):
        context = context_for(ORIGIN, query="The Great Novel")
        assert render("{query|lower|plus}", context) == "the+great+novel"

    def test_an_unresolvable_placeholder_is_an_error_by_default(self):
        # A URL silently missing an identifier requests the wrong page, which reads as the
        # site having changed rather than as a spec bug.
        with pytest.raises(TemplateError, match="no value in this context"):
            render("{origin}/api/{vars.token}", context_for(ORIGIN))

    def test_it_can_be_lenient_when_asked(self):
        assert render("{origin}/{vars.token}", context_for(ORIGIN), strict=False) == (
            "https://example.com/"
        )

    def test_a_template_with_no_placeholders_is_returned_as_is(self):
        assert render("https://example.com/all", context_for(ORIGIN)) == "https://example.com/all"

    def test_a_missing_var_is_distinguished_from_an_empty_one(self):
        context = context_for(ORIGIN, {"token": ""})
        assert render("{origin}/{vars.token}", context) == "https://example.com/"

    def test_context_for_drops_absent_extras(self):
        assert "page" not in context_for(ORIGIN, page=None)


class TestClosedSets:
    def test_the_filter_set_is_exactly_what_the_rfc_names(self):
        assert set(FILTERS) == {"plus", "urlencode", "urlencode_plus", "lower", "slug"}

    def test_the_placeholder_set_is_exactly_what_the_rfc_names(self):
        assert set(PLACEHOLDER_ROOTS) == {
            "origin",
            "vars",
            "query",
            "novel_url",
            "request_url",
            "page",
            "chapter",
            "item",
            "username",
            "password",
        }


class TestRenderUrl:
    """A template cannot know whether the placeholder before its `/` already ends in one."""

    def test_a_doubled_slash_in_the_path_collapses(self):
        context = context_for("https://e.test", novel_url="https://e.test/manga/a-title/")
        assert (
            render_url("{novel_url}/ajax/chapters/", context)
            == "https://e.test/manga/a-title/ajax/chapters/"
        )

    def test_the_scheme_keeps_its_own_slashes(self):
        assert render_url("{origin}/x", context_for("https://e.test")) == "https://e.test/x"

    def test_a_query_string_keeps_a_doubled_slash(self):
        # A `//` after the `?` can be data: a redirect target, or a path a site passes along.
        rendered = render_url("{origin}/go?to=//other.test/x", context_for("https://e.test"))
        assert rendered == "https://e.test/go?to=//other.test/x"

    def test_a_single_slash_is_untouched(self):
        context = context_for("https://e.test", novel_url="https://e.test/manga/a-title")
        assert (
            render_url("{novel_url}/ajax/chapters/", context)
            == "https://e.test/manga/a-title/ajax/chapters/"
        )
