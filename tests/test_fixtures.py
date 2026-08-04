"""Recording a site and replaying it offline.

The value of a fixture is that a difference means the *spec* changed. So the tests are about
what a replay notices and what it deliberately does not.
"""

import pytest

from sourcelib.cli import main
from sourcelib.fetch import FetchError, RecordedFetcher
from sourcelib.fixtures import (
    Recording,
    RecordingFetcher,
    compare,
    hosts,
    load,
    replay,
    save,
)
from sourcelib.trial import run_trial

SPEC_YAML = """spec: 1
base_url: https://e.test/
novel:
  title: { css: h1 }
  cover: { css: figure img, attr: data-src }
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
  <h1>Reborn</h1>
  <figure><img data-src="/c.jpg"/></figure>
  <ul class="list">
    <li><a href="/c/1">Ch 1</a></li>
    <li><a href="/c/2">Ch 2</a></li>
    <li><a href="/c/3">Ch 3</a></li>
  </ul>
</body></html>"""

BODY = '<html><body><div id="content"><p>Words here.</p></div></body></html>'


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "e.test.yaml").write_text(SPEC_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def live():
    pages = {"https://e.test/novel/x": NOVEL}
    pages.update({f"https://e.test/c/{n}": BODY for n in range(1, 4)})
    return RecordedFetcher(pages)


def record(repo, live, url="https://e.test/novel/x"):
    recorder = RecordingFetcher(live)
    trial = run_trial(repo / "specs" / "e.test.yaml", url, recorder, root=repo)
    assert trial.ok, trial.error
    save(repo, "e.test", Recording(url, recorder.pages, trial.summary))
    return trial


class TestRecording:
    def test_it_keeps_every_page_the_spec_fetched(self, repo, live):
        record(repo, live)
        recording = load(repo, "e.test")
        # The novel page plus the three chapters it sampled.
        assert len(recording.pages) == 4

    def test_it_keys_by_the_requested_url(self, repo, live):
        record(repo, live)
        assert "https://e.test/novel/x" in load(repo, "e.test").pages

    def test_it_stores_the_result_that_was_known_good(self, repo, live):
        record(repo, live)
        expected = load(repo, "e.test").expected
        assert expected["title"] == "Reborn"
        assert expected["chapters"] == 3

    def test_it_stores_body_lengths_rather_than_bodies(self, repo, live):
        record(repo, live)
        bodies = load(repo, "e.test").expected["bodies"]
        assert all(set(b) == {"id", "characters"} for b in bodies)

    def test_it_is_listed_by_host(self, repo, live):
        record(repo, live)
        assert hosts(repo) == ["e.test"]

    def test_no_fixtures_folder_lists_nothing(self, repo):
        assert hosts(repo) == []

    def test_writing_the_same_recording_twice_is_byte_identical(self, repo, live):
        # mtime=0, so re-recording unchanged bytes is not a diff in the repository.
        record(repo, live)
        first = (repo / "fixtures" / "e.test" / "recording.json.gz").read_bytes()
        record(repo, live)
        assert (repo / "fixtures" / "e.test" / "recording.json.gz").read_bytes() == first


class TestReplay:
    def test_an_unchanged_spec_replays_clean(self, repo, live):
        record(repo, live)
        ok, differences = replay(repo, "e.test", repo / "specs" / "e.test.yaml")
        assert ok and differences == []

    def test_it_needs_no_network(self, repo, live):
        record(repo, live)
        # Nothing but the recording: the live fetcher is not passed in at all.
        ok, _ = replay(repo, "e.test", repo / "specs" / "e.test.yaml")
        assert ok

    def test_a_broken_selector_is_reported_as_a_difference(self, repo, live):
        record(repo, live)
        spec = repo / "specs" / "e.test.yaml"
        spec.write_text(SPEC_YAML.replace("css: figure img", "css: figure.absent img"))
        ok, differences = replay(repo, "e.test", spec)
        assert not ok
        assert any("cover_url" in d for d in differences)

    def test_a_changed_chapter_count_is_reported(self, repo, live):
        record(repo, live)
        spec = repo / "specs" / "e.test.yaml"
        spec.write_text(SPEC_YAML.replace("css: ul.list li", "css: ul.list li:first-child"))
        ok, differences = replay(repo, "e.test", spec)
        assert not ok
        assert any("chapters: expected 3, got 1" in d for d in differences)

    def test_a_spec_reaching_an_unrecorded_url_says_to_re_record(self, repo, live):
        record(repo, live)
        spec = repo / "specs" / "e.test.yaml"
        spec.write_text(
            SPEC_YAML.replace('css: "#content"', 'css: "#content"').replace(
                "  request: { page: novel }", "  request: { get: '{origin}/toc' }"
            )
        )
        ok, differences = replay(repo, "e.test", spec)
        assert not ok
        assert any("Re-record" in d for d in differences)

    def test_a_missing_page_raises_rather_than_passing_quietly(self, repo, live):
        record(repo, live)
        fetcher = load(repo, "e.test").fetcher()
        with pytest.raises(FetchError, match="not in the recording"):
            fetcher.fetch("GET", "https://e.test/never-seen")


class TestCompare:
    def test_identical_summaries_differ_in_nothing(self):
        summary = {"title": "T", "chapters": 3, "bodies": []}
        assert compare(summary, dict(summary)) == []

    def test_a_difference_names_both_sides(self):
        differences = compare({"title": "Old"}, {"title": "New"})
        assert differences == ["title: expected 'Old', got 'New'"]

    def test_a_long_value_is_shortened(self):
        differences = compare({"synopsis": "x" * 200}, {"synopsis": "y"})
        assert all(len(d) < 160 for d in differences)

    def test_a_body_length_change_is_reported_per_chapter(self):
        expected = {"bodies": [{"id": 1, "characters": 100}]}
        actual = {"bodies": [{"id": 1, "characters": 40}]}
        assert "chapter 1: body was 100 characters, now 40" in compare(expected, actual)[0]

    def test_a_key_absent_from_the_recording_is_not_compared(self):
        # An older recording lacking a field should not fail against a newer summary.
        assert compare({"title": "T"}, {"title": "T", "language": "en"}) == []


class TestCliFixtures:
    def test_check_fixtures_replays_everything(self, repo, live, capsys):
        record(repo, live)
        assert main(["check", "--root", str(repo), "--fixtures"]) == 0
        assert "1 of 1 recordings replay unchanged" in capsys.readouterr().out

    def test_check_fixtures_fails_on_a_regression(self, repo, live, capsys):
        record(repo, live)
        (repo / "specs" / "e.test.yaml").write_text(SPEC_YAML.replace("css: h1", "css: h2"))
        assert main(["check", "--root", str(repo), "--fixtures"]) == 1
        assert "e.test" in capsys.readouterr().err

    def test_a_recording_whose_spec_is_gone_is_reported(self, repo, live, capsys):
        record(repo, live)
        (repo / "specs" / "e.test.yaml").unlink()
        assert main(["check", "--root", str(repo), "--fixtures"]) == 1
        assert "is gone" in capsys.readouterr().err

    def test_no_recordings_is_only_an_error_under_strict(self, repo, capsys):
        assert main(["check", "--root", str(repo), "--fixtures"]) == 0
        capsys.readouterr()
        assert main(["check", "--root", str(repo), "--fixtures", "--strict"]) == 1
