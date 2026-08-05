"""Requirements on a resolved spec, per RFC-0001 sections 3.2, 3.3 and 3.6."""

import pytest

from sourcelib.spec.checks import Problem, check_resolved, derived_capabilities
from sourcelib.spec.loader import load_document

SERVABLE = {
    "spec": 1,
    "base_url": "https://example.com/",
    "novel": {"title": {"css": "h1"}},
    "toc": {"request": {"page": "novel"}, "items": {"css": "a"}},
    "chapter": {"body": {"css": "#content"}},
}


def problems(**overrides):
    document = {**SERVABLE, **overrides}
    return [p.field for p in check_resolved(load_document(document))]


class TestServable:
    def test_a_complete_spec_has_no_problems(self):
        assert check_resolved(load_document(SERVABLE)) == []

    def test_a_novel_stage_is_required(self):
        assert "novel" in problems(novel=None)

    def test_chapters_must_come_from_somewhere(self):
        assert "toc.items" in problems(toc={"request": {"page": "novel"}})
        assert "toc.items" in problems(toc=None)

    def test_a_chapter_body_must_come_from_somewhere(self):
        assert "chapter.body" in problems(chapter={})
        assert "chapter.body" in problems(chapter=None)

    def test_an_empty_novel_stage_is_enough(self):
        # Every field is optional because the interpreter falls back to page metadata, so
        # declaring the stage is the spec saying it relies on that.
        assert check_resolved(load_document({**SERVABLE, "novel": {}})) == []


class TestExempt:
    def test_an_abstract_spec_is_not_required_to_be_servable(self):
        # It exists to be extended, so it may declare a fragment of a source.
        document = {"spec": 1, "novel": {"title": {"css": "h1"}}}
        assert check_resolved(load_document(document)) == []

    def test_a_disabled_spec_is_exempt(self):
        # A never-implemented host is three lines, which is the point of one mechanism
        # covering both "down" and "never built".
        document = {
            "spec": 1,
            "base_url": "https://dead.example/",
            "disabled": "Domain expired",
        }
        assert check_resolved(load_document(document)) == []


class TestHooksSatisfy:
    def test_a_hook_stands_in_for_its_stage(self):
        document = {
            "spec": 1,
            "base_url": "https://example.com/",
            "hooks": {
                "novel.request": "hooks/sites/example.com.py",
                "toc.items": "hooks/sites/example.com.py",
                "chapter.body": "hooks/sites/example.com.py",
            },
        }
        assert check_resolved(load_document(document)) == []

    def test_a_whole_file_cannot_be_disproved_offline(self):
        # A bare path binds whatever the file defines, and learning that means importing
        # it. So it satisfies every requirement here and the hook loader reports the rest.
        document = {
            "spec": 1,
            "base_url": "https://example.com/",
            "hooks": "hooks/sites/example.com.py",
        }
        assert check_resolved(load_document(document)) == []

    def test_the_wrong_hook_does_not_stand_in(self):
        document = {
            "spec": 1,
            "base_url": "https://example.com/",
            "novel": {"title": {"css": "h1"}},
            "toc": {"request": {"page": "novel"}, "items": {"css": "a"}},
            "hooks": {"novel.cover": "hooks/sites/example.com.py"},
        }
        assert [p.field for p in check_resolved(load_document(document))] == ["chapter.body"]


class TestAddresses:
    def test_search_must_declare_an_address(self):
        assert "search.request" in problems(search={"css": ".row"})

    def test_search_with_only_pagination_is_refused(self):
        assert "search.request" in problems(
            search={"request": {"paginate": {"while": "has_items", "url": "u"}}, "css": ".row"}
        )

    def test_toc_must_declare_an_address(self):
        assert "toc.request" in problems(toc={"items": {"css": "a"}})

    def test_chapter_may_carry_only_pagination(self):
        # Its address is the URL the table of contents captured.
        assert (
            problems(
                chapter={
                    "request": {"paginate": {"next": {"css": "a.next", "attr": "href"}}},
                    "body": {"css": "#content"},
                }
            )
            == []
        )

    def test_novel_may_carry_only_pagination(self):
        assert problems(novel={"request": {"headers": {"x-a": "b"}}, "title": {"css": "h1"}}) == []

    def test_a_hooked_request_needs_no_address(self):
        assert (
            problems(
                toc={"items": {"css": "a"}},
                hooks={"toc.request": "hooks/sites/example.com.py"},
            )
            == []
        )


class TestClaims:
    def test_can_search_true_without_a_search_stage_is_refused(self):
        assert "can_search" in problems(can_search=True)

    def test_can_search_true_with_a_search_stage_is_fine(self):
        assert problems(can_search=True, search={"request": {"get": "u"}, "css": ".r"}) == []

    def test_can_search_false_is_always_allowed(self):
        # An off switch for a child inheriting a search endpoint that has broken.
        assert problems(can_search=False) == []

    def test_can_login_true_without_a_login_hook_is_refused(self):
        assert "can_login" in problems(can_login=True)

    def test_can_login_true_with_a_login_hook_is_fine(self):
        assert problems(can_login=True, hooks={"login": "hooks/sites/example.com.py"}) == []


class TestDerivedCapabilities:
    def test_a_search_stage_derives_can_search(self):
        spec = load_document(SERVABLE)
        assert derived_capabilities(spec, set())["can_search"] is False
        with_search = load_document({**SERVABLE, "search": {"request": {"get": "u"}}})
        assert derived_capabilities(with_search, set())["can_search"] is True

    def test_a_bound_login_hook_derives_can_login(self):
        spec = load_document(SERVABLE)
        assert derived_capabilities(spec, set())["can_login"] is False
        assert derived_capabilities(spec, {"login"})["can_login"] is True

    def test_binding_an_unrelated_hook_does_not_derive_login(self):
        # The bug this guards: deriving from the declaration would report can_login for
        # every source that carries any hook file at all.
        spec = load_document(SERVABLE)
        assert derived_capabilities(spec, {"chapter.body"})["can_login"] is False

    def test_an_explicit_false_beats_derivation(self):
        spec = load_document({**SERVABLE, "search": {"request": {"get": "u"}}, "can_search": False})
        assert derived_capabilities(spec, set())["can_search"] is False


class TestProblem:
    def test_it_reads_as_a_field_and_a_reason(self):
        assert str(Problem("toc.items", "must exist")) == "toc.items: must exist"

    @pytest.mark.parametrize(
        ("a", "b", "equal"),
        [
            (Problem("a", "x"), Problem("a", "x"), True),
            (Problem("a", "x"), Problem("a", "y"), False),
            (Problem("a", "x"), "a: x", False),
        ],
    )
    def test_equality(self, a, b, equal):
        assert (a == b) is equal


class TestPageOrder:
    """Section 3.6: a `page` must name a request that has already run when it is evaluated."""

    def test_reusing_the_novel_page_for_the_toc_is_fine(self):
        assert problems() == []

    def test_a_stage_cannot_reuse_its_own_document(self):
        # The natural typo: `page: novel` inside the novel stage reads as "the novel page".
        # It used to reach the fetcher and fail there, naming the cache rather than the spec.
        found = problems(novel={"request": {"page": "novel"}, "title": {"css": "h1"}})
        assert "novel.request.page" in found

    def test_a_forward_reference_is_refused(self):
        found = problems(novel={"request": {"page": "toc"}, "title": {"css": "h1"}})
        assert "novel.request.page" in found

    def test_an_alternative_inside_from_is_checked_too(self):
        found = problems(
            toc={"request": {"from": [{"page": "chapter"}]}, "items": {"css": "a"}},
        )
        assert "toc.request.page" in found

    def test_a_chapter_may_reuse_the_toc_document(self):
        assert problems(chapter={"request": {"page": "toc"}, "body": {"css": "#content"}}) == []

    def test_a_named_request_from_an_earlier_stage_resolves(self):
        assert (
            problems(
                novel={"request": {"name": "landing"}, "title": {"css": "h1"}},
                toc={"request": {"page": "landing"}, "items": {"css": "a"}},
            )
            == []
        )


class TestPipesAreChecked:
    """Section 6: a step a pipe names must exist, and consecutive steps must connect.

    `validate_pipe` could always say both. Until this ran on a resolved spec, a misspelled
    step passed validation and raised once per chapter mid-crawl.
    """

    def test_an_unknown_step_is_named_with_its_field(self):
        found = problems(chapter={"body": {"css": "#content", "pipe": ["trimm"]}})
        assert "chapter.body.pipe" in found

    def test_a_pipe_whose_types_do_not_connect_is_refused(self):
        found = problems(chapter={"body": {"css": "#content", "pipe": ["text", "unwrap_all"]}})
        assert "chapter.body.pipe" in found

    def test_a_valid_pipe_passes(self):
        assert problems(chapter={"body": {"css": "#content", "pipe": ["paragraphs"]}}) == []

    def test_a_pipe_inside_a_fallback_is_checked(self):
        found = problems(
            novel={"title": {"css": "h1", "fallback": [{"css": "h2", "pipe": ["nope"]}]}}
        )
        assert any(field.startswith("novel.title.fallback") for field in found)

    def test_an_item_field_pipe_is_checked(self):
        found = problems(
            toc={
                "request": {"page": "novel"},
                "items": {"css": "a", "fields": {"title": {"pipe": ["nope"]}}},
            }
        )
        assert any("items" in field for field in found)

    def test_a_named_pipe_is_expanded_before_checking(self):
        # The name is a reference, so the steps behind it are what has to connect.
        found = problems(
            pipes={"clean": ["trimm"]},
            chapter={"body": {"css": "#content", "pipe": ["clean"]}},
        )
        assert "pipes.clean" in found

    def test_a_named_pipe_that_is_valid_passes_through_a_reference(self):
        assert (
            problems(
                pipes={"clean": [{"strip_tags": ["h3"]}, "paragraphs"]},
                chapter={"body": {"css": "#content", "pipe": ["clean"]}},
            )
            == []
        )

    def test_a_step_appended_after_a_named_pipe_must_still_connect(self):
        # This is how a child adds a step without respelling the base's pipe, so the join
        # between the expansion and what follows is the part worth checking.
        found = problems(
            pipes={"clean": ["paragraphs"]},
            chapter={"body": {"css": "#content", "pipe": ["clean", "unwrap_all"]}},
        )
        assert "chapter.body.pipe" in found

    def test_a_pipe_naming_no_such_pipe_is_refused(self):
        found = problems(chapter={"body": {"css": "#content", "pipe": "no_such_pipe"}})
        assert "chapter.body.pipe" in found

    def test_a_pipe_named_by_string_resolves(self):
        assert (
            problems(
                pipes={"clean": ["paragraphs"]},
                chapter={"body": {"css": "#content", "pipe": "clean"}},
            )
            == []
        )


class TestRequireNamesAField:
    """A misspelled `require` would drop every row and look exactly like a dead selector."""

    def test_an_undeclared_name_is_refused(self):
        found = problems(
            toc={
                "request": {"page": "novel"},
                "items": {"css": "a", "fields": {"url": {"attr": "href"}}, "require": ["parnet"]},
            }
        )
        assert "toc.items.require" in found

    def test_a_declared_name_passes(self):
        assert (
            problems(
                toc={
                    "request": {"page": "novel"},
                    "items": {
                        "css": "a",
                        "fields": {"url": {"attr": "href"}, "parent": {"attr": "data-parent"}},
                        "require": ["parent"],
                    },
                }
            )
            == []
        )

    def test_a_search_stage_is_checked_too(self):
        found = problems(
            search={
                "request": {"get": "https://e/s?q={query}"},
                "css": "a",
                "fields": {"url": {"attr": "href"}},
                "require": ["nope"],
            }
        )
        assert "search.require" in found


class TestTemplatesAreChecked:
    """Section 4.2: both sets are closed, and a name out of scope is a load-time error.

    Nothing called the validator, so all three of these passed `check` and were discovered by a
    crawl fetching the wrong page.
    """

    def test_an_unknown_placeholder_is_refused(self):
        assert "toc.request.get" in problems(
            toc={"request": {"get": "{origin}/{novel_titel}"}, "items": {"css": "a"}}
        )

    def test_an_unknown_filter_is_refused(self):
        assert "search.request.get" in problems(
            search={"request": {"get": "{origin}/s?q={query|urlencodee}"}, "css": "a"}
        )

    def test_a_placeholder_out_of_its_stage_is_refused(self):
        found = problems(toc={"request": {"get": "{origin}/s?q={query}"}, "items": {"css": "a"}})
        assert "toc.request.get" in found

    def test_query_is_legal_in_a_search_request(self):
        assert (
            problems(
                search={"request": {"get": "{origin}/s?q={query|urlencode}"}, "css": "a"},
            )
            == []
        )

    def test_page_is_legal_only_inside_a_paginate_url(self):
        paged = {
            "request": {
                "page": "novel",
                "paginate": {"last": 5, "url": "{novel_url}/page-{page}"},
            },
            "items": {"css": "a"},
        }
        assert problems(toc=paged) == []
        assert "toc.request.get" in problems(
            toc={"request": {"get": "{origin}/{page}"}, "items": {"css": "a"}}
        )

    def test_item_is_legal_only_inside_an_item_list(self):
        assert (
            problems(
                toc={
                    "request": {"page": "novel"},
                    "items": {
                        "css": "a",
                        "fields": {"cid": {"attr": "data-id"}, "url": "{origin}/c/{item.cid}"},
                    },
                }
            )
            == []
        )
        assert "novel.title.const" in problems(novel={"title": {"const": "{item.cid}"}})

    def test_a_const_is_a_template(self):
        assert "novel.title.const" in problems(novel={"title": {"const": "{vars.nope|nofilter}"}})
        assert (
            problems(
                vars={"slug": {"on": "url", "regex": "/novel/([^/]+)"}},
                novel={"title": {"const": "{vars.slug}"}},
            )
            == []
        )

    def test_a_fallback_const_is_checked(self):
        assert "novel.title.fallback[0].const" in problems(
            novel={"title": {"css": "h1", "fallback": [{"const": "{nothing}"}]}}
        )

    def test_headers_may_only_name_what_resolves_everywhere(self):
        assert "headers.Referer" in problems(headers={"Referer": "{novel_url}"})
        assert problems(headers={"Referer": "{origin}/"}) == []

    def test_a_payload_and_a_request_header_are_checked(self):
        found = problems(
            search={
                "request": {
                    "post": "{origin}/s",
                    "payload": {"q": "{quer}"},
                    "headers": {"X-Token": "{vars.absent|nope}"},
                },
                "css": "a",
            }
        )
        assert "search.request.payload.q" in found
        assert "search.request.headers.X-Token" in found
