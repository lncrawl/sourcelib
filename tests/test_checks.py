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
