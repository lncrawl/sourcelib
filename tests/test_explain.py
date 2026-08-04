"""The structural digest.

Asserted on the things a spec author reads off it: the chapter-list candidate and its row
count, which data script carries values, and where a page count would come from.
"""

import pytest

from sourcelib.explain import explain, format_digest, selector_for
from sourcelib.spec.extract import Document

PAGE = """<html lang="en"><head>
  <title>Reborn as a Sword - ReadSite</title>
  <meta property="og:title" content="Reborn as a Sword"/>
  <meta property="og:image" content="/covers/real.jpg"/>
  <meta name="description" content="A blade awakens."/>
  <script type="application/ld+json">
    {"@type":"Book","name":"Reborn as a Sword","author":{"name":"Ada"},"description":"d"}
  </script>
  <script id="__NEXT_DATA__" type="application/json">
    {"props":{"pageProps":{"novel":{"title":"Reborn","id":77}}},"page":"/novel"}
  </script>
  <script>var chapImages = ["a.jpg","b.jpg"];</script>
</head><body>
  <nav class="site-nav"><a href="/latest">Latest</a><a href="/popular">Popular</a></nav>
  <h1 class="novel-title">Reborn as a Sword</h1>
  <figure class="cover"><img data-src="/covers/real.jpg" src="/ph.gif"/></figure>
  <ul class="chapter-list">
    <li class="chapter-item"><a href="/c/1" data-id="11">Chapter 1</a></li>
    <li class="chapter-item"><a href="/c/2" data-id="12">Chapter 2</a></li>
    <li class="chapter-item"><a href="/c/3" data-id="13">Chapter 3</a></li>
    <li class="chapter-item"><a href="/c/4" data-id="14">Chapter 4</a></li>
  </ul>
  <div class="pager"><a href="?p=1">1</a><a href="?p=2">2</a><a href="?p=17">17</a>
    <a href="?p=2">Next</a></div>
</body></html>"""


def one(markup, selector):
    """The single element *selector* matches, for the selector-naming tests."""
    node = Document.from_html(markup).node
    assert node is not None
    found = node.select_one(selector)
    assert found is not None
    return found


@pytest.fixture
def digest():
    return explain(Document.from_html(PAGE, url="https://read.test/novel/x"))


class TestSelectorFor:
    def test_an_id_wins(self):
        assert selector_for(one('<div id="content" class="a b"/>', "div")) == "div#content"

    def test_classes_are_used_when_there_is_no_id(self):
        node = one('<li class="chapter-item odd"/>', "li")
        assert selector_for(node) == "li.chapter-item.odd"

    def test_a_bare_tag_when_there_is_nothing_else(self):
        assert selector_for(one("<article/>", "article")) == "article"

    @pytest.mark.parametrize(
        "value",
        ["css-1a2b3c4d", "jsx-2847561930", "a1b2c3d4e5", "averyverylongclassnamethatgoeson"],
    )
    def test_a_generated_looking_class_is_not_offered(self, value):
        # Selecting on a bundler's hash produces a spec that breaks on the next deploy.
        assert selector_for(one(f'<li class="{value}"/>', "li")) == "li"


class TestMetadata:
    def test_opengraph_is_reported(self, digest):
        assert digest.meta["og:title"] == "Reborn as a Sword"
        assert digest.meta["og:image"] == "/covers/real.jpg"

    def test_the_description_meta_is_reported(self, digest):
        assert digest.meta["description"] == "A blade awakens."

    def test_json_ld_keys_are_listed(self, digest):
        assert "name" in digest.json_ld and "author" in digest.json_ld

    def test_the_language_and_title_tag_are_reported(self, digest):
        assert digest.lang == "en"
        assert "Reborn as a Sword" in digest.title


class TestDataScripts:
    def test_a_json_script_reports_its_selector_and_keys(self, digest):
        entry = next(e for e in digest.data_scripts if "__NEXT_DATA__" in e["selector"])
        assert entry["selector"] == "script#__NEXT_DATA__"
        assert entry["keys"] == ["page", "props"]

    def test_a_javascript_literal_is_flagged_as_needing_regex(self):
        # A JS literal is not JSON, so `json:` cannot read it however inviting it looks.
        page = (
            '<script id="__NUXT__">window.__NUXT__ = (function(a,b){return '
            '{data:[{novel:{title:a,id:b}}]}}("Reborn",77));</script>'
        )
        entry = explain(Document.from_html(page)).data_scripts[0]
        assert entry["selector"] == "script#__NUXT__"
        assert entry["keys"] == [] and "regex" in entry["note"]

    def test_a_trivial_inline_script_is_not_reported(self):
        assert explain(Document.from_html('<script id="x">var a=1;</script>')).data_scripts == []

    def test_an_array_at_the_top_level_says_so(self):
        page = '<script id="rows">[{"id":1,"title":"t"}]</script>'
        entry = explain(Document.from_html(page)).data_scripts[0]
        assert entry["keys"] == ["$ is an array"]
        assert entry["row_keys"] == ["id", "title"]

    def test_a_script_with_neither_id_nor_json_is_skipped(self):
        page = "<script>document.write('a very long line of ordinary script');</script>"
        assert explain(Document.from_html(page)).data_scripts == []


class TestHeadingsAndCover:
    def test_the_heading_carries_a_usable_selector(self, digest):
        assert digest.headings[0] == {
            "selector": "h1.novel-title",
            "text": "Reborn as a Sword",
        }

    def test_a_lazily_loaded_image_is_offered_as_the_cover(self, digest):
        cover = digest.covers[0]
        assert cover["selector"] == "figure.cover img"
        assert "data-src" in cover["attr"]

    def test_a_plain_image_is_not_offered(self):
        page = '<div><img src="/banner.png"/></div>'
        assert explain(Document.from_html(page)).covers == []


class TestGroups:
    def test_the_chapter_list_is_the_largest_group(self, digest):
        biggest = digest.groups[0]
        assert biggest.rows == 4
        assert biggest.selector == "ul.chapter-list li.chapter-item"

    def test_it_reports_a_sample_row(self, digest):
        biggest = digest.groups[0]
        assert biggest.sample_text == "Chapter 1"
        assert biggest.sample_href == "/c/1"

    def test_it_reports_other_attributes_a_row_carries(self, digest):
        # This is how an author learns a row has an id to template a URL from.
        assert "data-id" in digest.groups[0].attributes

    def test_a_two_link_menu_is_not_a_group(self, digest):
        # The site nav has two links, below the threshold, so it is not offered as a list.
        assert all("site-nav" not in g.selector for g in digest.groups)

    def test_groups_are_ordered_largest_first(self, digest):
        assert digest.groups == sorted(digest.groups, key=lambda g: g.rows, reverse=True)

    def test_a_page_with_no_repetition_says_so(self):
        digest = explain(Document.from_html("<html><body><h1>Only this</h1></body></html>"))
        assert digest.groups == []
        assert "No repeated structure" in format_digest(digest)


class TestPagination:
    def test_a_numeric_pager_reports_its_highest_link(self, digest):
        pager = next(p for p in digest.pagination if "pager" in p["selector"])
        assert pager["highest"] == 17
        assert "max" in pager["hint"]

    def test_a_next_link_is_recognised(self, digest):
        pager = next(p for p in digest.pagination if "pager" in p["selector"])
        assert "Next" in pager["next_text"]

    @pytest.mark.parametrize("label", ["Next", "next chapter", "下一章", "More"])
    def test_several_spellings_of_next_are_recognised(self, label):
        page = f'<div class="nav"><a href="/a">1</a><a href="/b">{label}</a></div>'
        digest = explain(Document.from_html(page))
        assert digest.pagination and digest.pagination[0].get("next_text")


class TestOutput:
    def test_the_text_form_is_short_enough_to_read(self, digest):
        text = format_digest(digest)
        # The whole point is replacing a several-hundred-kilobyte page with this.
        assert len(text) < 4000
        assert len(text.splitlines()) < 60

    def test_it_names_every_section_it_found(self, digest):
        text = format_digest(digest)
        for heading in (
            "METADATA",
            "DATA SCRIPTS",
            "HEADINGS",
            "REPEATED STRUCTURES",
            "PAGINATION",
        ):
            assert heading in text

    def test_it_shows_the_row_count_beside_the_selector(self, digest):
        assert "4 rows  ul.chapter-list li.chapter-item" in format_digest(digest)

    def test_the_json_form_carries_the_same_content(self, digest):
        data = digest.to_dict()
        assert data["groups"][0]["rows"] == 4
        assert data["meta"]["og:title"] == "Reborn as a Sword"

    def test_it_survives_a_document_with_no_markup(self):
        assert format_digest(explain(Document(url="https://e.test/")))
