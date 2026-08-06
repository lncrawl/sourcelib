"""End to end: a resolved spec against a recorded site.

The assertions are what a reviewer actually checks per CONTRIBUTING — the chapter count, the
order, one body — plus the failure rules from section 4.4.
"""

import pytest

from sourcelib.fetch import RecordedFetcher
from sourcelib.runtime import CrawlError, Interpreter, Report
from sourcelib.spec.model import SourceSpec

NOVEL_PAGE = """<html lang="en"><head>
  <meta property="og:title" content="Meta Title"/>
  <meta property="og:image" content="/og-cover.jpg"/>
  <meta property="og:description" content="A description from OpenGraph."/>
</head><body>
  <h1 class="title">Reborn as a Sword</h1>
  <figure><img data-src="/covers/real.jpg" src="/ph.gif"/></figure>
  <div class="author">Author: Ada, Grace</div>
  <div class="tags"><a>#Action</a><a>Drama</a><a>#Action</a></div>
  <div class="summary"><p>A blade awakens.</p><p>Then <em>everything</em> changed.</p></div>
  <ul class="list">
    <li class="vol">Volume One</li>
    <li class="ch"><a href="/c/1">Ch 1</a></li>
    <li class="ch"><a href="/c/2">Ch 2</a></li>
    <li class="vol">Volume Two</li>
    <li class="ch"><a href="/c/3">Ch 3</a></li>
  </ul>
</body></html>"""

CHAPTER = """<html><body>
  <div id="content">
    <div class="ads">BUY NOW</div>
    <p>Ch 1: The Gate</p>
    <p>He walked <span>slowly</span> onward.</p>
  </div>
</body></html>"""

SPEC = {
    "spec": 1,
    "base_url": "https://e.test/",
    "language": "fr",
    "novel": {
        "title": {"css": "h1.title"},
        "cover": {"css": "figure img", "attr": ["data-src", "src"]},
        "authors": {
            "css": "div.author",
            "pipe": [{"strip_prefix": "Author:"}, "trim", {"split": ","}, "trim"],
        },
        "tags": {"css": "div.tags a", "all": True, "pipe": [{"strip_prefix": "#"}, "trim"]},
        "synopsis": {"css": "div.summary"},
    },
    "toc": {
        "request": {"page": "novel"},
        "items": {
            "css": "li.ch",
            "fields": {"title": {"css": "a"}, "url": {"css": "a", "attr": "href"}},
        },
        "volumes": {"css": "li.vol", "fields": {"title": {}}},
    },
    "chapter": {
        "body": {
            "css": "#content",
            "pipe": [
                {"strip_css": [".ads"]},
                {"drop_leading": {"matches": r"^Ch \d+"}},
                {"unwrap": ["span"]},
                "paragraphs",
            ],
        }
    },
}


def spec(**overrides):
    return SourceSpec.model_validate({**SPEC, **overrides})


@pytest.fixture
def site():
    return RecordedFetcher(
        {
            "https://e.test/novel/x": NOVEL_PAGE,
            "https://e.test/c/1": CHAPTER,
            "https://e.test/c/2": CHAPTER,
            "https://e.test/c/3": CHAPTER,
        }
    )


class TestReadNovel:
    def test_it_reads_every_declared_field(self, site):
        novel = Interpreter(spec(), site).read_novel("https://e.test/novel/x")
        assert novel.title == "Reborn as a Sword"
        assert novel.cover_url == "https://e.test/covers/real.jpg"
        assert novel.authors == ["Ada", "Grace"]
        assert novel.synopsis == "<p>A blade awakens.</p><p>Then <em>everything</em> changed.</p>"

    def test_a_declared_pipe_replaces_the_default_rather_than_extending_it(self, site):
        # SPEC declares a tags pipe, so the tags default's `unique` does not run and the
        # site's duplicate survives. Section 6.4: what runs is always visible in the file.
        novel = Interpreter(spec(), site).read_novel("https://e.test/novel/x")
        assert novel.tags == ["Action", "Drama", "Action"]

    def test_naming_the_default_steps_deduplicates(self, site):
        deduped = dict(SPEC["novel"])
        deduped["tags"] = {
            "css": "div.tags a",
            "all": True,
            "pipe": [{"strip_prefix": "#"}, "trim", "collapse_spaces", "drop_empty", "unique"],
        }
        novel = Interpreter(spec(novel=deduped), site).read_novel("https://e.test/novel/x")
        assert novel.tags == ["Action", "Drama"]

    def test_an_undeclared_tags_pipe_gets_the_dedupe_default(self, site):
        plain = dict(SPEC["novel"])
        plain["tags"] = {"css": "div.tags a", "all": True}
        novel = Interpreter(spec(novel=plain), site).read_novel("https://e.test/novel/x")
        assert novel.tags == ["#Action", "Drama"]

    def test_the_author_property_joins_for_the_crawler(self, site):
        novel = Interpreter(spec(), site).read_novel("https://e.test/novel/x")
        assert novel.author == "Ada, Grace"

    def test_manga_and_mtl_flags_carry_through(self, site):
        novel = Interpreter(spec(has_manga=True, has_mtl=True), site).read_novel(
            "https://e.test/novel/x"
        )
        assert novel.is_manga is True and novel.is_mtl is True

    def test_the_toc_reuses_the_novel_document(self, site):
        Interpreter(spec(), site).read_novel("https://e.test/novel/x")
        # page: novel means one fetch, not two.
        assert [url for _, url in site.calls] == ["https://e.test/novel/x"]


class TestMetadataFallback:
    def test_it_falls_back_to_opengraph(self, site):
        bare = spec(novel={"title": {"css": ".absent"}})
        novel = Interpreter(bare, site).read_novel("https://e.test/novel/x")
        assert novel.title == "Meta Title"

    def test_a_declared_field_wins_over_metadata(self, site):
        novel = Interpreter(spec(), site).read_novel("https://e.test/novel/x")
        assert novel.title == "Reborn as a Sword"

    def test_the_cover_and_synopsis_fall_back_too(self, site):
        bare = spec(novel={"title": {"css": "h1.title"}})
        novel = Interpreter(bare, site).read_novel("https://e.test/novel/x")
        assert novel.cover_url == "https://e.test/og-cover.jpg"
        assert "OpenGraph" in novel.synopsis


class TestLanguage:
    def test_detection_beats_the_declared_default(self, site):
        # The spec says fr; the document says en. Section 3.2 puts detection strongest.
        novel = Interpreter(spec(), site).read_novel("https://e.test/novel/x")
        assert novel.language == "en"

    def test_the_spec_default_is_used_when_nothing_is_detected(self):
        fetcher = RecordedFetcher(
            {
                "https://e.test/novel/x": NOVEL_PAGE.replace('<html lang="en">', "<html>"),
                "https://e.test/c/1": CHAPTER,
            }
        )
        novel = Interpreter(spec(), fetcher).read_novel("https://e.test/novel/x")
        assert novel.language == "fr"


class TestTableOfContents:
    def test_chapters_are_numbered_from_one_in_order(self, site):
        novel = Interpreter(spec(), site).read_novel("https://e.test/novel/x")
        assert [c.id for c in novel.chapters] == [1, 2, 3]
        assert [c.title for c in novel.chapters] == ["Ch 1", "Ch 2", "Ch 3"]

    def test_urls_are_absolute(self, site):
        novel = Interpreter(spec(), site).read_novel("https://e.test/novel/x")
        assert novel.chapters[0].url == "https://e.test/c/1"

    def test_interleaved_headings_partition_the_chapters(self, site):
        novel = Interpreter(spec(), site).read_novel("https://e.test/novel/x")
        assert [c.volume for c in novel.chapters] == [1, 1, 2]
        assert [(v.id, v.title) for v in novel.volumes] == [(1, "Volume One"), (2, "Volume Two")]

    def test_without_declared_volumes_chapters_group_by_size(self, site):
        no_volumes = dict(SPEC["toc"])
        no_volumes.pop("volumes")
        novel = Interpreter(spec(toc=no_volumes, chapters_per_volume=2), site).read_novel(
            "https://e.test/novel/x"
        )
        assert [c.volume for c in novel.chapters] == [1, 1, 2]

    def test_most_sources_declare_nothing_and_get_one_volume(self, site):
        no_volumes = dict(SPEC["toc"])
        no_volumes.pop("volumes")
        novel = Interpreter(spec(toc=no_volumes), site).read_novel("https://e.test/novel/x")
        assert [v.id for v in novel.volumes] == [1]

    def test_extra_row_fields_reach_the_chapter(self, site):
        toc = {
            "request": {"page": "novel"},
            "items": {
                "css": "li.ch",
                "fields": {
                    "title": {"css": "a"},
                    "url": {"css": "a", "attr": "href"},
                    "cid": {
                        "css": "a",
                        "attr": "href",
                        "pipe": [{"regex": {"pattern": r"/c/(\d+)"}}],
                    },
                },
            },
        }
        novel = Interpreter(spec(toc=toc), site).read_novel("https://e.test/novel/x")
        assert novel.chapters[0].extras["cid"] == "1"
        # And it is readable as {chapter.cid} when building the chapter's own request.
        assert novel.chapters[0].context()["cid"] == "1"


class TestDownloadChapter:
    def test_the_body_is_cleaned_by_its_pipe(self, site):
        interpreter = Interpreter(spec(), site)
        novel = interpreter.read_novel("https://e.test/novel/x")
        chapter = interpreter.download_chapter(novel, novel.chapters[0])
        assert chapter.body == "<p>He walked slowly onward.</p>"
        assert chapter.success is True

    def test_the_duplicated_heading_is_dropped(self, site):
        interpreter = Interpreter(spec(), site)
        novel = interpreter.read_novel("https://e.test/novel/x")
        chapter = interpreter.download_chapter(novel, novel.chapters[0])
        assert chapter.body is not None and "Ch 1: The Gate" not in chapter.body

    def test_advertising_is_stripped(self, site):
        interpreter = Interpreter(spec(), site)
        novel = interpreter.read_novel("https://e.test/novel/x")
        chapter = interpreter.download_chapter(novel, novel.chapters[0])
        assert chapter.body is not None and "BUY NOW" not in chapter.body

    def test_a_url_template_builds_the_chapter_address(self):
        fetcher = RecordedFetcher(
            {"https://e.test/novel/x": NOVEL_PAGE, "https://e.test/read/1": CHAPTER}
        )
        toc = {
            "request": {"page": "novel"},
            "items": {
                "css": "li.ch",
                "fields": {
                    "title": {"css": "a"},
                    "url": {"css": "a", "attr": "href"},
                    "cid": {
                        "css": "a",
                        "attr": "href",
                        "pipe": [{"regex": {"pattern": r"/c/(\d+)"}}],
                    },
                },
            },
        }
        chapter_stage = {**SPEC["chapter"], "url": "{origin}/read/{chapter.cid}"}
        interpreter = Interpreter(spec(toc=toc, chapter=chapter_stage), fetcher)
        novel = interpreter.read_novel("https://e.test/novel/x")
        interpreter.download_chapter(novel, novel.chapters[0])
        assert ("GET", "https://e.test/read/1") in fetcher.calls

    def test_an_address_read_off_a_page_is_not_a_template(self):
        """A site's own braces are content, not a placeholder.

        lnmtl renders its chapter table client-side and serves the unrendered
        `{{ chapter.site_url }}` as the href. Passing that through the URL renderer failed the
        crawl naming a placeholder nobody wrote.
        """
        listing = NOVEL_PAGE.replace('href="/c/1"', 'href="/c/{{ chapter.site_url }}"')
        fetcher = RecordedFetcher(
            {
                "https://e.test/novel/x": listing,
                "https://e.test/c/{{ chapter.site_url }}": CHAPTER,
            }
        )
        interpreter = Interpreter(spec(), fetcher)
        novel = interpreter.read_novel("https://e.test/novel/x")
        chapter = interpreter.download_chapter(novel, novel.chapters[0])
        assert ("GET", "https://e.test/c/{{ chapter.site_url }}") in fetcher.calls
        assert "He walked" in (chapter.body or "")


class TestFailureRules:
    """Section 4.4: uneven on purpose."""

    def test_a_missing_title_is_an_error(self, site):
        bare = {
            "spec": 1,
            "base_url": "https://e.test/",
            "novel": {"title": {"css": ".absent"}},
            "toc": SPEC["toc"],
            "chapter": SPEC["chapter"],
        }
        stripped = NOVEL_PAGE.replace('<meta property="og:title" content="Meta Title"/>', "")
        stripped = stripped.replace("<title>", "").replace("</title>", "")
        fetcher = RecordedFetcher({"https://e.test/novel/x": stripped})
        with pytest.raises(CrawlError) as caught:
            Interpreter(SourceSpec.model_validate(bare), fetcher).read_novel(
                "https://e.test/novel/x"
            )
        assert caught.value.field == "novel.title"

    def test_an_empty_chapter_list_is_an_error(self):
        fetcher = RecordedFetcher(
            {"https://e.test/novel/x": "<html><body><h1 class='title'>T</h1></body></html>"}
        )
        with pytest.raises(CrawlError) as caught:
            Interpreter(spec(), fetcher).read_novel("https://e.test/novel/x")
        assert caught.value.field == "toc.items"

    def test_missing_optional_fields_are_warnings_not_errors(self):
        page = """<html><body><h1 class="title">T</h1>
          <ul class="list"><li class="ch"><a href="/c/1">Ch 1</a></li></ul>
        </body></html>"""
        fetcher = RecordedFetcher({"https://e.test/novel/x": page})
        report = Report()
        novel = Interpreter(spec(), fetcher, report).read_novel("https://e.test/novel/x")
        # Real pages omit these often enough that failing would reject working sources.
        assert novel.title == "T"
        assert any("cover" in w for w in report.warnings)
        assert any("tags" in w for w in report.warnings)

    def test_an_empty_body_is_an_error_naming_the_field(self, site):
        interpreter = Interpreter(spec(chapter={"body": {"css": ".absent"}}), site)
        novel = interpreter.read_novel("https://e.test/novel/x")
        with pytest.raises(CrawlError) as caught:
            interpreter.download_chapter(novel, novel.chapters[0])
        assert caught.value.field == "chapter.body"


class TestReport:
    def test_skipped_rows_are_counted(self, site):
        toc = {
            "request": {"page": "novel"},
            "items": {
                "css": "li",  # matches the volume headings too
                "fields": {
                    "title": {"css": "a"},
                    "url": {"css": "a", "attr": "href"},
                },
            },
        }
        report = Report()
        novel = Interpreter(spec(toc=toc), site, report).read_novel("https://e.test/novel/x")
        assert len(novel.chapters) == 3
        # The two heading rows have no url, so they were dropped rather than emitted.
        assert report.skipped["toc"] == 2


class TestSearch:
    def test_a_source_without_a_search_stage_returns_nothing(self, site):
        assert Interpreter(spec(), site).search("anything") == []

    def test_results_carry_title_url_and_info(self):
        results_page = """<html><body><ul>
          <li class="row"><a href="/n/1" title="One">One</a><span class="i">12 ch</span></li>
          <li class="row"><a href="/n/2" title="Two">Two</a><span class="i">30 ch</span></li>
        </ul></body></html>"""
        fetcher = RecordedFetcher({"https://e.test/search?q=sword": results_page})
        search = {
            "request": {"get": "{origin}/search?q={query|plus}"},
            "css": "li.row",
            "fields": {
                "title": {"css": "a", "attr": "title"},
                "url": {"css": "a", "attr": "href"},
                "info": {"css": "span.i"},
            },
        }
        results = Interpreter(spec(search=search), fetcher).search("sword")
        assert [(r.title, r.url, r.info) for r in results] == [
            ("One", "https://e.test/n/1", "12 ch"),
            ("Two", "https://e.test/n/2", "30 ch"),
        ]


class TestTocFromAlternatives:
    """`from` means "until one yields items", so the toc has to supply the predicate.

    Without it the first alternative that merely fetched won, which made the fallback list
    decorative: a Madara host whose ajax endpoint answers 200 with an empty body would have
    reported zero chapters instead of falling through to the page that has them.
    """

    def test_an_alternative_that_fetches_but_holds_no_rows_loses(self, site):
        site.pages["https://e.test/ajax"] = "<html><body><ul></ul></body></html>"
        interpreter = Interpreter(
            spec(
                toc={
                    "request": {"from": [{"get": "{origin}/ajax"}, {"page": "novel"}]},
                    "items": {
                        "css": "li.ch",
                        "fields": {"title": {"css": "a"}, "url": {"css": "a", "attr": "href"}},
                    },
                }
            ),
            site,
        )
        novel = interpreter.read_novel("https://e.test/novel/x")
        assert [c.title for c in novel.chapters] == ["Ch 1", "Ch 2", "Ch 3"]
