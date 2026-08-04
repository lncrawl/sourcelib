"""Constraints the model enforces on a single document.

Only rules that are *always* errors belong here. A rule a parent could satisfy, such as a
stage needing an address, cannot be checked against a raw child that inherits it.
"""

import pytest
from pydantic import ValidationError

from sourcelib.spec.loader import load_document
from sourcelib.spec.model import Extractor, Paginate, Request, SourceSpec, hook_points

MINIMAL = {"spec": 1, "base_url": "https://example.com/"}


def spec(**overrides):
    return load_document({**MINIMAL, **overrides})


class TestKeyNames:
    """RFC-0001 section 3.9.1: a document key never carries a language workaround."""

    def test_document_key_is_accepted(self):
        assert Paginate.model_validate({"while": "has_items", "url": "{origin}/p/{page}"})

    @pytest.mark.parametrize(
        ("model", "payload"),
        [
            (Paginate, {"while_": "has_items"}),
            (Request, {"from_": [{"get": "https://example.com/"}]}),
            (Extractor, {"json_": "a.b"}),
        ],
    )
    def test_private_name_is_refused(self, model, payload):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            model.model_validate(payload)

    def test_an_unknown_key_is_refused(self):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            spec(nvoel={})


class TestExtractorSources:
    def test_css_may_combine_with_json(self):
        assert Extractor.model_validate({"css": "script#data", "json": "props.title"})

    def test_css_may_combine_with_regex(self):
        assert Extractor.model_validate({"css": "script", "regex": r"id=(\d+)"})

    def test_no_source_reads_the_node_in_scope(self):
        assert Extractor.model_validate({"attr": "href"})

    @pytest.mark.parametrize("other", [{"css": "h1"}, {"json": "a"}, {"regex": "x"}])
    def test_const_stands_alone(self, other):
        with pytest.raises(ValidationError, match="const cannot be combined"):
            Extractor.model_validate({"const": "x", **other})

    @pytest.mark.parametrize("other", [{"css": "h1"}, {"json": "a"}])
    def test_header_stands_alone(self, other):
        with pytest.raises(ValidationError, match="header cannot be combined"):
            Extractor.model_validate({"header": "X-Total", **other})

    def test_json_and_regex_cannot_read_the_same_element(self):
        with pytest.raises(ValidationError, match="json and regex"):
            Extractor.model_validate({"css": "script", "json": "a", "regex": "x"})


class TestPaginate:
    @pytest.mark.parametrize(
        "payload",
        [
            {"while": "has_items", "url": "{origin}/{page}"},
            {"last": {"css": ".last"}, "url": "{origin}/{page}"},
            {"next": {"css": "a.next", "attr": "href"}},
        ],
    )
    def test_each_termination_alone_is_valid(self, payload):
        assert Paginate.model_validate(payload)

    def test_two_terminations_are_refused(self):
        with pytest.raises(ValidationError, match="only one of while, last, next"):
            Paginate.model_validate({"while": "has_items", "last": {"css": "b"}})

    def test_url_is_invalid_with_next(self):
        with pytest.raises(ValidationError, match="url is invalid with next"):
            Paginate.model_validate({"next": {"css": "a"}, "url": "{origin}/{page}"})

    def test_concurrent_is_refused_with_next(self):
        # A next link is only known once the page holding it has been read, so there is nothing to
        # run in parallel. `count` and `while` both address pages by number and can.
        with pytest.raises(ValidationError, match="concurrent is invalid with next"):
            Paginate.model_validate({"next": {"css": "a"}, "concurrent": True})

    def test_concurrent_with_while_is_valid(self):
        assert Paginate.model_validate(
            {"while": "has_items", "url": "u", "concurrent": True}
        ).concurrent

    def test_concurrent_with_count_is_valid(self):
        assert Paginate.model_validate(
            {"last": {"css": ".last"}, "url": "u", "concurrent": True}
        ).concurrent


class TestRequest:
    def test_two_addresses_are_refused(self):
        with pytest.raises(ValidationError, match="only one of get, post, page, from"):
            Request.model_validate({"get": "https://a/", "page": "novel"})

    def test_payload_needs_post(self):
        with pytest.raises(ValidationError, match="payload is valid only with post"):
            Request.model_validate({"get": "https://a/", "payload": {"q": "x"}})

    def test_get_plus_form_may_carry_a_payload(self):
        # The two-step: fetch the page holding the form, harvest it, post to its action.
        # A search stage has no document in scope, so this is its only declarative route.
        assert Request.model_validate(
            {
                "get": "https://a/search.html",
                "form": ".search-container form",
                "payload": {"keyboard": "{query}"},
            }
        )

    def test_form_without_an_address_still_refuses_a_payload(self):
        with pytest.raises(ValidationError, match="payload is valid only with post"):
            Request.model_validate({"form": "form", "payload": {"q": "x"}})

    def test_wait_for_needs_render(self):
        with pytest.raises(ValidationError, match="wait_for is meaningless without render"):
            Request.model_validate({"get": "https://a/", "wait_for": ".ready"})

    def test_no_address_is_allowed_here(self):
        # A novel or chapter request inherits its stage's URL and may declare only
        # paginate. Whether an address is required depends on the stage, so it is not a
        # rule this model can enforce.
        assert Request.model_validate({"paginate": {"next": {"css": "a.next"}}})

    def test_has_address_reports_the_difference(self):
        assert Request.model_validate({"get": "https://a/"}).has_address
        assert not Request.model_validate({"paginate": {"next": {"css": "a"}}}).has_address


class TestVar:
    def test_url_scope(self):
        assert spec(vars={"id": {"on": "url", "regex": r"/(\d+)/"}})

    def test_own_request_is_a_request(self):
        assert spec(vars={"token": {"on": {"get": "{origin}/api/token"}, "json": "token"}})

    def test_own_request_cannot_reference_a_stage_document(self):
        with pytest.raises(ValidationError, match="cannot use page"):
            spec(vars={"t": {"on": {"page": "novel"}, "json": "t"}})

    @pytest.mark.parametrize(
        "url",
        [
            "{origin}/api?q={query}",
            "{novel_url}/token",
            "{origin}/api?c={chapter.id}",
            "{origin}/api?p={page}",
        ],
    )
    def test_own_request_cannot_use_a_shorter_lived_placeholder(self, url):
        # A session-scoped var outlives every one of these.
        with pytest.raises(ValidationError, match="cannot use"):
            spec(vars={"t": {"on": {"get": url}, "json": "t"}})

    def test_own_request_may_use_origin_and_other_vars(self):
        assert spec(
            vars={
                "a": {"on": "url", "regex": r"/(\d+)"},
                "t": {"on": {"get": "{origin}/api/{vars.a}"}, "json": "t"},
            }
        )


class TestHookPoints:
    def test_derived_set_covers_every_stage_field(self):
        points = hook_points()
        assert "novel.synopsis" in points
        assert "toc.volumes" in points
        assert "chapter.body" in points
        assert {"check_response", "login"} <= points

    def test_every_stage_has_a_request_point(self):
        points = hook_points()
        for stage in ("search", "novel", "toc", "chapter"):
            assert f"{stage}.request" in points

    def test_a_file_binding_every_point_is_valid(self):
        assert spec(hooks="hooks/sites/example.com.py")

    def test_named_points_are_valid(self):
        assert spec(hooks={"chapter.body": "hooks/sites/example.com.py"})

    @pytest.mark.parametrize("stale", ["chapter_body", "novel_soup", "parse_summary", "toc_items"])
    def test_flat_names_are_refused(self, stale):
        with pytest.raises(ValidationError, match="unknown hook point"):
            spec(hooks={stale: "hooks/sites/example.com.py"})

    def test_a_field_the_stage_does_not_define_is_refused(self):
        with pytest.raises(ValidationError, match="unknown hook point"):
            spec(hooks={"novel.chapters": "hooks/sites/example.com.py"})


class TestSourceSpec:
    def test_a_two_line_alias_is_valid(self):
        # Declaring no stages is the whole point of extends; the resolved document is
        # where the requirement applies.
        assert load_document(
            {"spec": 1, "base_url": "https://mirror.example/", "extends": "specs/example.com.yaml"}
        )

    def test_an_abstract_spec_needs_no_base_url(self):
        assert load_document({"spec": 1, "novel": {"title": {"css": "h1"}}})

    def test_spec_version_is_required(self):
        with pytest.raises(ValidationError, match="spec"):
            load_document({"base_url": "https://example.com/"})

    @pytest.mark.parametrize("url", ["example.com", "//example.com", "ftp://example.com"])
    def test_base_url_must_be_absolute_http(self, url):
        with pytest.raises(ValidationError, match="absolute http"):
            spec(base_url=url)

    @pytest.mark.parametrize("code", ["eng", "EN", "e", "en-US"])
    def test_language_must_be_iso_639_1(self, code):
        with pytest.raises(ValidationError, match="ISO 639-1"):
            spec(language=code)

    def test_rate_limit_must_be_positive(self):
        with pytest.raises(ValidationError):
            spec(rate_limit=0)

    def test_extra_item_fields_are_preserved(self):
        parsed = spec(
            toc={
                "items": {"css": ".row", "fields": {"url": {"attr": "href"}, "cid": {"json": "id"}}}
            }
        )
        assert parsed.toc is not None
        assert parsed.toc.items is not None
        assert set(parsed.toc.items.fields) == {"url", "cid"}

    def test_an_item_field_may_be_a_url_template(self):
        parsed = spec(
            toc={
                "items": {
                    "css": ".row",
                    "fields": {"cid": {"json": "id"}, "url": "{origin}/c/{item.cid}"},
                }
            }
        )
        assert parsed.toc and parsed.toc.items
        assert parsed.toc.items.fields["url"] == "{origin}/c/{item.cid}"


class TestRoundTrip:
    def test_dumping_by_alias_produces_document_keys(self):
        parsed = SourceSpec.model_validate(
            {**MINIMAL, "toc": {"request": {"paginate": {"while": "has_items", "url": "u"}}}}
        )
        dumped = parsed.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
        assert dumped["toc"]["request"]["paginate"]["while"] == "has_items"
        assert "while_" not in dumped["toc"]["request"]["paginate"]

    def test_a_dumped_document_validates_again(self):
        original = {
            **MINIMAL,
            "vars": {"id": {"on": "url", "regex": r"/(\d+)/"}},
            "novel": {"title": {"css": "h1"}},
            "toc": {"items": {"css": "a", "fields": {"url": {"attr": "href"}}}},
            "chapter": {"body": {"css": "#content"}},
        }
        once = load_document(original)
        twice = load_document(once.model_dump(by_alias=True, exclude_defaults=True))
        assert twice == once

    def test_unset_concurrent_is_on_where_it_can_be(self):
        for condition in ({"last": {"css": "b"}}, {"while": "has_items"}):
            assert Paginate.model_validate({**condition, "url": "u"}).runs_concurrently

    def test_unset_concurrent_is_off_with_next(self):
        # Not an error, just inapplicable: nothing declared it and it cannot apply.
        assert not Paginate.model_validate({"next": {"css": "a"}}).runs_concurrently

    def test_false_forces_one_at_a_time(self):
        assert not Paginate.model_validate(
            {"last": {"css": "b"}, "url": "u", "concurrent": False}
        ).runs_concurrently


class TestPaginateStep:
    def test_it_defaults_to_one(self):
        assert Paginate.model_validate({"last": 3, "url": "https://e/p={page}"}).step == 1

    def test_zero_is_refused(self):
        # A step of zero would address the same page forever.
        with pytest.raises(ValidationError):
            Paginate.model_validate({"while": "has_items", "step": 0, "url": "https://e/{page}"})

    def test_a_negative_step_is_refused(self):
        with pytest.raises(ValidationError):
            Paginate.model_validate({"while": "has_items", "step": -5, "url": "https://e/{page}"})

    def test_a_page_size_is_a_valid_step(self):
        paginate = Paginate.model_validate(
            {"while": "has_items", "step": 100, "url": "https://e/?start={page}"}
        )
        assert paginate.step == 100

    def test_it_does_not_prevent_concurrency(self):
        # Every address is computable up front whatever the stride, so a strided walk parallelises
        # exactly as a page-numbered one does.
        paginate = Paginate.model_validate(
            {"last": 500, "step": 100, "url": "https://e/?start={page}"}
        )
        assert paginate.runs_concurrently is True
