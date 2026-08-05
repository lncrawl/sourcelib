"""Reading repeated structures, per RFC-0001 section 3.8."""

import pytest

from sourcelib.spec.extract import Document
from sourcelib.spec.items import (
    Row,
    assign_volumes,
    group_by_size,
    read_items,
    read_rows,
    sort_rows,
)
from sourcelib.spec.model import ItemList

TOC = """<html><body>
  <nav><a href="/latest">Latest</a></nav>
  <ul class="list">
    <li><a href="/c/1" data-id="11">Chapter 1</a></li>
    <li><a href="/c/2" data-id="12">Chapter 2</a></li>
  </ul>
</body></html>"""


def items(**fields):
    return ItemList.model_validate(fields)


@pytest.fixture
def toc():
    return Document.from_html(TOC, url="https://e.test/book/x")


class TestReadRows:
    def test_it_reads_one_row_per_container(self, toc):
        rows, skipped = read_rows(items(css="ul.list li", fields={"title": {"css": "a"}}), toc)
        assert [r.get("title") for r in rows] == ["Chapter 1", "Chapter 2"]
        assert skipped == 0

    def test_fields_are_scoped_to_their_row(self, toc):
        rows, _ = read_rows(
            items(
                css="ul.list li",
                fields={"title": {"css": "a"}, "url": {"css": "a", "attr": "href"}},
            ),
            toc,
            kinds={"url": "url"},
        )
        assert rows[0].get("url") == "https://e.test/c/1"

    def test_extra_keys_are_preserved(self, toc):
        # This is how a chapter carries an identifier to the request that fetches it.
        rows, _ = read_rows(
            items(css="ul.list li", fields={"cid": {"css": "a", "attr": "data-id"}}), toc
        )
        assert rows[0].get("cid") == "11"

    def test_a_row_missing_a_required_field_is_skipped(self, toc):
        # A loose selector plus a narrowing pipe, rather than a perfect selector.
        rows, skipped = read_rows(
            items(
                css="a",
                fields={
                    "url": {
                        "attr": "href",
                        "pipe": [{"regex": {"pattern": r"^/c/\d+$"}}],
                    }
                },
            ),
            toc,
            required=["url"],
        )
        assert len(rows) == 2 and skipped == 1

    def test_the_skipped_count_is_reported(self, toc):
        _, skipped = read_rows(
            items(css="a", fields={"url": {"css": ".absent"}}), toc, required=["url"]
        )
        # A large number means the selector is wrong even though the crawl succeeded.
        assert skipped == 3

    def test_rows_keep_their_document_order(self, toc):
        rows, _ = read_rows(items(css="ul.list li", fields={"t": {"css": "a"}}), toc)
        assert [r.order for r in rows] == [0, 1]


class TestFieldTemplates:
    def test_a_field_may_template_over_earlier_siblings(self):
        document = Document.from_html(
            '<div class="row" data-serie="7" data-slug="abc"></div>', url="https://e.test/"
        )
        rows, _ = read_rows(
            items(
                css=".row",
                fields={
                    "serie_id": {"attr": "data-serie"},
                    "slug": {"attr": "data-slug"},
                    "url": "{origin}/en/serie-{item.serie_id}/f{item.slug}",
                },
            ),
            document,
        )
        assert rows[0].get("url") == "https://e.test/en/serie-7/fabc"

    def test_referencing_a_later_field_is_an_error_not_an_empty_string(self):
        from sourcelib.interpolate import TemplateError

        document = Document.from_html('<div class="row" data-a="1"></div>', url="https://e.test/")
        # Fields evaluate in declaration order, so `a` is not visible to `url` yet. Rendering
        # it empty would build a URL that requests the wrong page and reads as a site change.
        with pytest.raises(TemplateError, match=r"\{item.a\} has no value"):
            read_rows(
                items(css=".row", fields={"url": "{origin}/{item.a}", "a": {"attr": "data-a"}}),
                document,
            )


class TestJsonRows:
    def test_rows_may_be_json_objects(self):
        document = Document.from_json(
            {"data": [{"title": "One", "raw_id": 7}, {"title": "Two", "raw_id": 8}]},
            url="https://e.test/api",
        )
        rows, _ = read_rows(
            items(json="data", fields={"title": {"json": "title"}, "rid": {"json": "raw_id"}}),
            document,
        )
        assert [r.get("title") for r in rows] == ["One", "Two"]
        assert rows[0].get("rid") == "7"

    def test_a_bare_array_at_the_top_level(self):
        document = Document.from_json([{"link": "/a"}, {"link": "/b"}], url="https://e.test/api")
        rows, _ = read_rows(items(json="$", fields={"url": {"json": "link"}}), document)
        assert [r.get("url") for r in rows] == ["/a", "/b"]

    def test_a_json_field_may_be_templated(self):
        document = Document.from_json({"rows": [{"id": 5}]}, url="https://e.test/api")
        rows, _ = read_rows(
            items(
                json="rows",
                fields={"cid": {"json": "id"}, "url": "{origin}/c/{item.cid}"},
            ),
            document,
        )
        assert rows[0].get("url") == "https://e.test/c/5"


class TestParseThenSelect:
    """`json` with `css` on one list: the path names markup, the selector runs inside it."""

    FRAGMENT = (
        '<div class="novel-list">'
        '<div class="novel-item"><a href="/n/one"><h4 class="novel-title">One</h4></a></div>'
        '<div class="novel-item"><a href="/n/two"><h4 class="novel-title">Two</h4></a></div>'
        "</div>"
    )

    def spec(self):
        return items(
            json="resultview",
            css=".novel-list .novel-item a",
            fields={"title": {"css": ".novel-title"}, "url": {"attr": "href"}},
        )

    def test_a_fragment_inside_a_json_field(self):
        document = Document.from_json({"resultview": self.FRAGMENT}, url="https://e.test/search")
        rows, _ = read_rows(self.spec(), document, required=("url",))
        assert [r.get("title") for r in rows] == ["One", "Two"]
        assert rows[0].get("url") == "/n/one"

    def test_a_list_of_fragments_selects_across_all_of_them(self):
        document = Document.from_json(
            {"resultview": ['<div class="novel-item"><a href="/n/a">A</a></div>', self.FRAGMENT]},
            url="https://e.test/search",
        )
        rows, _ = read_rows(
            items(json="resultview", css=".novel-item a", fields={"url": {"attr": "href"}}),
            document,
            required=("url",),
        )
        assert [r.get("url") for r in rows] == ["/n/a", "/n/one", "/n/two"]

    def test_the_spec_parser_is_used(self):
        document = Document.from_json({"resultview": self.FRAGMENT}, url="https://e.test/search")
        rows, _ = read_rows(self.spec(), document, required=("url",), parser="html.parser")
        assert [r.get("title") for r in rows] == ["One", "Two"]

    def test_a_path_that_holds_no_markup_yields_no_rows(self):
        document = Document.from_json({"resultview": {"not": "markup"}}, url="https://e.test/s")
        rows, _ = read_rows(self.spec(), document, required=("url",))
        assert rows == []

    def test_css_alone_still_reads_an_html_document(self, toc):
        rows, _ = read_rows(
            items(css="ul.list li", fields={"url": {"css": "a", "attr": "href"}}),
            toc,
            required=("url",),
        )
        assert len(rows) == 2


class TestSorting:
    def test_sort_by_is_numeric(self):
        document = Document.from_html(
            "".join(f'<li data-n="{n}">Ch {n}</li>' for n in (10, 2, 33, 1)), url="https://e/"
        )
        spec = items(css="li", sort_by="n", fields={"n": {"attr": "data-n"}})
        rows, _ = read_items(spec, document)
        assert [r.get("n") for r in rows] == ["1", "2", "10", "33"]

    def test_a_non_numeric_value_sorts_after_every_number(self):
        document = Document.from_html(
            '<li data-n="2">a</li><li data-n="x">b</li><li data-n="1">c</li>', url="https://e/"
        )
        rows, _ = read_items(
            items(css="li", sort_by="n", fields={"n": {"attr": "data-n"}}), document
        )
        assert [r.get("n") for r in rows] == ["1", "2", "x"]

    def test_non_numeric_values_keep_their_relative_order(self):
        document = Document.from_html(
            '<li data-n="y">1</li><li data-n="x">2</li><li data-n="1">3</li>', url="https://e/"
        )
        rows, _ = read_items(
            items(css="li", sort_by="n", fields={"n": {"attr": "data-n"}}), document
        )
        assert [r.get("n") for r in rows] == ["1", "y", "x"]

    def test_reverse_applies_after_sorting(self):
        document = Document.from_html(
            '<li data-n="1">a</li><li data-n="3">b</li><li data-n="2">c</li>', url="https://e/"
        )
        spec = items(css="li", sort_by="n", reverse=True, fields={"n": {"attr": "data-n"}})
        rows, _ = read_items(spec, document)
        assert [r.get("n") for r in rows] == ["3", "2", "1"]

    def test_reverse_alone_flips_document_order(self):
        document = Document.from_html("<li>a</li><li>b</li>", url="https://e/")
        rows, _ = read_items(items(css="li", reverse=True, fields={"t": {}}), document)
        assert [r.get("t") for r in rows] == ["b", "a"]

    def test_document_order_is_the_default(self):
        document = Document.from_html(
            '<li data-n="9">a</li><li data-n="1">b</li>', url="https://e/"
        )
        rows, _ = read_items(items(css="li", fields={"n": {"attr": "data-n"}}), document)
        assert [r.get("n") for r in rows] == ["9", "1"]


class TestVolumes:
    """The interleaved shape real sites use: a heading row partitions what follows it."""

    MARKUP = """<ul class="list">
      <li class="vol">Volume One</li>
      <li class="ch"><a href="/c/1">Ch 1</a></li>
      <li class="ch"><a href="/c/2">Ch 2</a></li>
      <li class="vol">Volume Two</li>
      <li class="ch"><a href="/c/3">Ch 3</a></li>
    </ul>"""

    def test_each_chapter_takes_the_nearest_preceding_heading(self):
        document = Document.from_html(self.MARKUP, url="https://e/")
        chapters = items(css="li.ch", fields={"title": {"css": "a"}})
        volumes = items(css="li.vol", fields={"title": {}})
        chapter_rows, _ = read_rows(chapters, document)
        volume_rows, _ = read_rows(volumes, document)
        titles = assign_volumes(document, chapters, volumes, chapter_rows, volume_rows)
        assert [r.get("volume") for r in chapter_rows] == [1, 1, 2]
        assert titles == {1: "Volume One", 2: "Volume Two"}

    def test_chapters_before_the_first_heading_get_an_implicit_first_volume(self):
        markup = """<ul>
          <li class="ch"><a>Prologue</a></li>
          <li class="vol">Volume One</li>
          <li class="ch"><a>Ch 1</a></li>
        </ul>"""
        document = Document.from_html(markup, url="https://e/")
        chapters = items(css="li.ch", fields={"title": {"css": "a"}})
        volumes = items(css="li.vol", fields={"title": {}})
        chapter_rows, _ = read_rows(chapters, document)
        volume_rows, _ = read_rows(volumes, document)
        assign_volumes(document, chapters, volumes, chapter_rows, volume_rows)
        assert [r.get("volume") for r in chapter_rows] == [1, 1]

    def test_a_volume_field_on_the_row_wins_over_position(self):
        markup = """<ul>
          <li class="vol">Volume One</li>
          <li class="ch" data-vol="7"><a>Ch 1</a></li>
        </ul>"""
        document = Document.from_html(markup, url="https://e/")
        chapters = items(
            css="li.ch", fields={"title": {"css": "a"}, "volume": {"attr": "data-vol"}}
        )
        volumes = items(css="li.vol", fields={"title": {}})
        chapter_rows, _ = read_rows(chapters, document)
        volume_rows, _ = read_rows(volumes, document)
        assign_volumes(document, chapters, volumes, chapter_rows, volume_rows)
        assert chapter_rows[0].get("volume") == "7"

    def test_no_headings_leaves_assignment_to_the_caller(self):
        document = Document.from_html('<li class="ch">a</li>', url="https://e/")
        chapters = items(css="li.ch", fields={"t": {}})
        volumes = items(css="li.vol", fields={"title": {}})
        rows, _ = read_rows(chapters, document)
        assert assign_volumes(document, chapters, volumes, rows, []) == {}


class TestGroupBySize:
    """What most sources rely on: a volume every hundred chapters, declared nowhere."""

    @staticmethod
    def plain(count, preset=None):
        preset = preset or {}
        return [Row(dict(preset.get(i, {}))) for i in range(count)]

    def test_it_numbers_every_n_chapters(self):
        rows = self.plain(5)
        group_by_size(rows, 2)
        assert [r.get("volume") for r in rows] == [1, 1, 2, 2, 3]

    def test_the_default_puts_a_hundred_in_one_volume(self):
        rows = self.plain(101)
        group_by_size(rows, 100)
        assert rows[99].get("volume") == 1 and rows[100].get("volume") == 2

    def test_it_leaves_an_existing_volume_alone(self):
        rows = self.plain(2, preset={0: {"volume": 9}})
        group_by_size(rows, 100)
        assert [r.get("volume") for r in rows] == [9, 1]


class TestSortRowsDirectly:
    def test_it_is_stable_without_a_sort_key(self):
        document = Document.from_html("<li>a</li><li>b</li>", url="https://e/")
        spec = items(css="li", fields={"t": {}})
        rows, _ = read_rows(spec, document)
        assert [r.get("t") for r in sort_rows(rows, spec)] == ["a", "b"]


CATEGORIES = """<html><body><ul>
  <li class="row" data-parent="0"><a href="/category/alpha">Alpha</a></li>
  <li class="row" data-parent="7"><a href="/category/beta-sub">Beta sub</a></li>
  <li class="row" data-parent="0"><a href="/category/gamma">Gamma</a></li>
</ul></body></html>"""


class TestRequire:
    """Dropping a row on a field the stage does not need, per RFC-0001 section 3.8.

    The mechanism is the existing skip rule plus a filter step: `reject` yields nothing when it
    matches, so the field resolves empty and the row goes. Before this, a row could only be
    rejected on `url` or `title`, and none of the real cases read either.
    """

    def document(self):
        return Document.from_html(CATEGORIES, url="https://e/wp-json/categories")

    def spec(self, **extra):
        fields = {"title": {"css": "a"}, "url": {"css": "a", "attr": "href"}}
        fields.update(extra.pop("fields", {}))
        extra.setdefault("css", "li.row")
        return items(fields=fields, **extra)

    def test_without_require_every_row_survives(self):
        rows, skipped = read_rows(self.spec(), self.document(), required=("url",))
        assert len(rows) == 3
        assert skipped == 0

    def test_a_rejected_row_is_dropped_and_counted(self):
        # A child category has a non-zero parent, and only the parent field says so.
        spec = self.spec(
            fields={
                # No css: the attribute is on the row container itself, and a selector would
                # search inside it.
                "parent": {
                    "attr": "data-parent",
                    "pipe": [{"reject": {"pattern": "^[1-9]"}}],
                }
            },
            require=["parent"],
        )
        rows, skipped = read_rows(spec, self.document(), required=("url",))
        assert [row.get("title") for row in rows] == ["Alpha", "Gamma"]
        assert skipped == 1

    def test_regex_keeps_only_matching_rows(self):
        spec = self.spec(
            fields={"keep": {"css": "a", "pipe": [{"regex": {"pattern": "^Ga"}}]}},
            require=["keep"],
        )
        rows, _ = read_rows(spec, self.document(), required=("url",))
        assert [row.get("title") for row in rows] == ["Gamma"]

    def test_it_adds_to_rather_than_replaces_what_the_stage_requires(self):
        # A row with no url still goes, whatever `require` says.
        spec = self.spec(
            fields={"keep": {"const": "yes"}},
            css="li.row, nav a",
            require=["keep"],
        )
        rows, _ = read_rows(spec, self.document(), required=("url",))
        assert len(rows) == 3
