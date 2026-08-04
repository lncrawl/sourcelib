"""What `sourcelib try` reports.

The point of this module is that a failure says where to look. So the assertions are mostly
about the field name and the line number, not about the crawl itself.
"""

import json

import pytest

from sourcelib.fetch import RecordedFetcher
from sourcelib.models import Chapter, Novel
from sourcelib.spec.lines import line_map, locate
from sourcelib.trial import _sample, format_trial, run_trial

SPEC_YAML = """spec: 1
base_url: https://e.test/
novel:
  title: { css: h1.title }
  cover: { css: figure img, attr: [data-src, src] }
  authors: { css: div.author, all: true }
  tags: { css: div.tags a, all: true }
  synopsis: { css: div.summary }
toc:
  request: { page: novel }
  items:
    css: ul.list li
    fields:
      title: { css: a }
      url: { css: a, attr: href }
chapter:
  body: { css: "#content" }
"""

NOVEL = """<html><body>
  <h1 class="title">Reborn</h1>
  <figure><img data-src="/c.jpg"/></figure>
  <div class="author">Ada</div>
  <div class="tags"><a>Action</a></div>
  <div class="summary"><p>Blurb.</p></div>
  <ul class="list">
    <li><a href="/c/1">Ch 1</a></li>
    <li><a href="/c/2">Ch 2</a></li>
    <li><a href="/c/3">Ch 3</a></li>
    <li><a href="/c/4">Ch 4</a></li>
    <li><a href="/c/5">Ch 5</a></li>
  </ul>
</body></html>"""

BODY = '<html><body><div id="content"><p>Words here.</p></div></body></html>'


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "e.test.yaml").write_text(SPEC_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def site():
    pages = {"https://e.test/novel/x": NOVEL}
    pages.update({f"https://e.test/c/{n}": BODY for n in range(1, 6)})
    return RecordedFetcher(pages)


def run(repo, site, **kwargs):
    return run_trial(repo / "specs" / "e.test.yaml", "https://e.test/novel/x", site, repo, **kwargs)


class TestLineMap:
    def test_it_locates_a_nested_key(self):
        lines = line_map(SPEC_YAML)
        assert lines["novel.title"] == 4
        assert lines["toc.items.fields.url"] == 15

    def test_it_indexes_list_entries(self):
        lines = line_map("novel:\n  title:\n    fallback:\n      - { css: a }\n")
        assert "novel.title.fallback.0" in lines

    def test_locate_falls_back_to_the_nearest_ancestor(self):
        lines = line_map(SPEC_YAML)
        # A failure may name a path deeper than anything the document declares.
        assert locate(lines, "toc.items.url") == locate(lines, "toc.items")

    def test_locate_returns_none_for_an_unrelated_path(self):
        assert locate(line_map(SPEC_YAML), "nothing.like.this") is None

    def test_a_document_that_does_not_parse_yields_no_lines(self):
        assert line_map("a: [unclosed\n") == {}

    def test_an_empty_document_yields_no_lines(self):
        assert line_map("") == {}


class TestSuccess:
    def test_every_field_is_reported(self, repo, site):
        trial = run(repo, site)
        assert trial.ok is True
        names = [f.field for f in trial.findings]
        assert "novel.title" in names and "chapter.body" in names

    def test_it_counts_chapters_and_volumes(self, repo, site):
        trial = run(repo, site)
        assert trial.chapters == 5 and trial.volumes == 1

    def test_findings_carry_the_file_and_line(self, repo, site):
        title = next(f for f in run(repo, site).findings if f.field == "novel.title")
        assert title.file == "e.test.yaml" and title.line == 4

    def test_a_list_field_reports_its_count(self, repo, site):
        tags = next(f for f in run(repo, site).findings if f.field == "novel.tags")
        assert tags.count == 1

    def test_previews_are_bounded(self, repo, site):
        assert all(len(f.preview) <= 161 for f in run(repo, site).findings)


class TestChapterSampling:
    def test_it_takes_first_middle_and_last(self, repo, site):
        # First and last alone is exactly the sampling that lets a broken join through on a
        # body split across pages.
        trial = run(repo, site)
        assert [s["id"] for s in trial.sampled] == [1, 3, 5]

    def test_two_takes_the_ends(self, repo, site):
        assert [s["id"] for s in run(repo, site, sample=2).sampled] == [1, 5]

    def test_one_takes_the_first(self, repo, site):
        assert [s["id"] for s in run(repo, site, sample=1).sampled] == [1]

    def test_a_short_novel_is_taken_whole(self, repo):
        import re

        short = re.sub(r'<li><a href="/c/[2-5]">Ch [2-5]</a></li>\s*', "", NOVEL)
        site = RecordedFetcher({"https://e.test/novel/x": short, "https://e.test/c/1": BODY})
        trial = run(repo, site)
        assert trial.chapters == 1
        assert len(trial.sampled) == 1

    def test_each_sample_reports_its_size(self, repo, site):
        assert all(s["characters"] > 0 for s in run(repo, site).sampled)


class TestFailure:
    def test_a_broken_selector_names_the_field_and_the_line(self, repo, site):
        path = repo / "specs" / "e.test.yaml"
        path.write_text(SPEC_YAML.replace("css: h1.title", "css: h1.absent"), encoding="utf-8")
        trial = run(repo, site)
        assert trial.ok is False
        failure = next(f for f in trial.findings if not f.ok)
        # This is the whole point: where to look, not a traceback.
        assert failure.field == "novel.title"
        assert failure.line == 4

    def test_an_empty_chapter_list_is_reported(self, repo, site):
        path = repo / "specs" / "e.test.yaml"
        path.write_text(SPEC_YAML.replace("css: ul.list li", "css: ul.absent li"), encoding="utf-8")
        trial = run(repo, site)
        assert trial.ok is False
        assert any(f.field == "toc.items" for f in trial.findings if not f.ok)

    def test_a_broken_body_is_reported_per_chapter(self, repo, site):
        path = repo / "specs" / "e.test.yaml"
        path.write_text(SPEC_YAML.replace('css: "#content"', 'css: "#absent"'), encoding="utf-8")
        trial = run(repo, site)
        assert trial.ok is False
        assert all(not s["ok"] for s in trial.sampled)

    def test_a_document_that_does_not_parse_is_reported(self, repo, site):
        (repo / "specs" / "e.test.yaml").write_text("spec: 1\n  bad: indent\n", encoding="utf-8")
        trial = run(repo, site)
        assert trial.ok is False and trial.error

    def test_a_document_that_fails_validation_is_reported(self, repo, site):
        (repo / "specs" / "e.test.yaml").write_text(
            "spec: 1\nbase_url: https://e.test/\nnvoel: {}\n", encoding="utf-8"
        )
        trial = run(repo, site)
        assert trial.ok is False and "Extra inputs" in (trial.error or "")

    def test_an_unreachable_page_is_reported_not_raised(self, repo):
        trial = run(repo, RecordedFetcher({}))
        assert trial.ok is False and trial.error


class TestWarningsAndCounts:
    def test_a_missing_optional_field_warns_rather_than_failing(self, repo, site):
        path = repo / "specs" / "e.test.yaml"
        path.write_text(SPEC_YAML.replace("css: div.tags a", "css: div.absent a"), encoding="utf-8")
        trial = run(repo, site)
        assert any("tags" in w for w in trial.warnings)

    def test_skipped_rows_are_surfaced(self, repo, site):
        path = repo / "specs" / "e.test.yaml"
        path.write_text(SPEC_YAML.replace("css: ul.list li", "css: li"), encoding="utf-8")
        trial = run(repo, site)
        assert trial.chapters == 5


class TestOutput:
    def test_the_json_form_round_trips(self, repo, site):
        payload = json.dumps(run(repo, site).to_dict())
        assert json.loads(payload)["chapters"] == 5

    def test_the_text_form_names_the_verdict(self, repo, site):
        assert "PASSED" in format_trial(run(repo, site))

    def test_the_text_form_reminds_a_reviewer_what_to_read(self, repo, site):
        # Empty chapters and advert-filled bodies pass every automated check.
        assert "read one body" in format_trial(run(repo, site))

    def test_a_failure_shows_the_field_and_location(self, repo, site):
        path = repo / "specs" / "e.test.yaml"
        path.write_text(SPEC_YAML.replace("css: h1.title", "css: h1.absent"), encoding="utf-8")
        text = format_trial(run(repo, site))
        assert "FAIL" in text and "novel.title" in text and "e.test.yaml:4" in text

    def test_a_truncated_stage_is_mentioned(self, repo, site):
        path = repo / "specs" / "e.test.yaml"
        path.write_text(
            SPEC_YAML.replace(
                "  request: { page: novel }",
                "  request:\n    page: novel\n",
            ),
            encoding="utf-8",
        )
        assert "PASSED" in format_trial(run(repo, site))


class TestSampling:
    """`--sample N` has to actually fetch N chapters, spread across the list."""

    def novel(self, count):
        book = Novel(url="https://e.test/n")
        book.chapters = [
            Chapter(id=i, url=f"https://e.test/c/{i}", title=f"C{i}") for i in range(1, count + 1)
        ]
        return book

    @pytest.mark.parametrize("count", [1, 2, 3, 5, 20, 30])
    def test_it_returns_what_was_asked_for(self, count):
        # Every count above two used to collapse to first, middle and last, so a request for
        # twenty silently fetched three.
        assert len(_sample(self.novel(4649), count)) == count

    def test_the_first_and_last_are_always_included(self):
        picked = _sample(self.novel(4649), 20)
        assert picked[0].id == 1
        assert picked[-1].id == 4649

    @pytest.mark.parametrize("total", [10, 40, 73, 74, 100, 4649])
    def test_three_still_picks_exactly_what_it_used_to(self, total):
        """The default must not move: a fixture records the pages it sampled.

        An even count is where this broke. `round(36.5)` is 36, not 37, so a 74-chapter novel
        started asking for chapter 37 while its recording held chapter 38, and the replay reported
        a body of zero characters.
        """
        before = [1, total // 2 + 1, total]
        assert [c.id for c in _sample(self.novel(total), 3)] == before

    def test_the_spread_is_even(self):
        picked = [c.id for c in _sample(self.novel(1000), 5)]
        gaps = {picked[i + 1] - picked[i] for i in range(len(picked) - 1)}
        assert max(gaps) - min(gaps) <= 1

    def test_a_short_novel_yields_every_chapter_once(self):
        assert [c.id for c in _sample(self.novel(3), 20)] == [1, 2, 3]

    def test_it_is_deterministic(self):
        book = self.novel(4649)
        assert [c.id for c in _sample(book, 25)] == [c.id for c in _sample(book, 25)]
