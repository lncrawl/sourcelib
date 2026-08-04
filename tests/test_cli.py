"""The command line, including where its arguments are accepted.

`--root` sits on the subcommands rather than the top-level parser, because
`sourcelib check --root X` is where a reader expects to type it and argparse accepts a
top-level option only before the subcommand.
"""

import json

import pytest
import yaml

from sourcelib.cli import main

SERVABLE = (
    "spec: 1\n"
    "base_url: https://example.com/\n"
    "novel: { title: { css: h1 } }\n"
    "toc: { request: { page: novel }, items: { css: a } }\n"
    "chapter: { body: { css: '#content' } }\n"
)


@pytest.fixture
def repo(tmp_path):
    for folder in ("specs", "base", "disabled"):
        (tmp_path / folder).mkdir()
    (tmp_path / "specs" / "example.com.yaml").write_text(SERVABLE, encoding="utf-8")
    return tmp_path


class TestCheck:
    def test_root_is_accepted_after_the_subcommand(self, repo, capsys):
        assert main(["check", "--root", str(repo), str(repo / "specs")]) == 0
        assert "1 of 1 documents valid" in capsys.readouterr().out

    def test_root_is_inferred_from_a_folder_argument(self, repo, capsys):
        assert main(["check", str(repo / "specs")]) == 0
        assert "1 of 1 documents valid" in capsys.readouterr().out

    def test_a_resolved_problem_fails_and_names_the_field(self, repo, capsys):
        (repo / "specs" / "broken.example.yaml").write_text(
            "spec: 1\nbase_url: https://broken.example/\nnovel: {}\n", encoding="utf-8"
        )
        assert main(["check", "--root", str(repo), str(repo / "specs")]) == 1
        captured = capsys.readouterr()
        assert "toc.items" in captured.err
        assert "chapter.body" in captured.err

    def test_an_unresolvable_extends_fails(self, repo, capsys):
        (repo / "specs" / "orphan.example.yaml").write_text(
            "spec: 1\nbase_url: https://orphan.example/\nextends: base/absent.yaml\n",
            encoding="utf-8",
        )
        assert main(["check", "--root", str(repo), str(repo / "specs")]) == 1
        assert "does not exist" in capsys.readouterr().err

    def test_nothing_found_is_only_an_error_under_strict(self, repo, capsys):
        empty = repo / "base"
        assert main(["check", "--root", str(repo), str(empty)]) == 0
        capsys.readouterr()
        assert main(["check", "--root", str(repo), str(empty), "--strict"]) == 1

    def test_a_single_file_is_accepted(self, repo, capsys):
        assert main(["check", "--root", str(repo), str(repo / "specs" / "example.com.yaml")]) == 0
        assert "1 of 1" in capsys.readouterr().out


class TestResolve:
    def test_it_prints_yaml_by_default(self, repo, capsys):
        assert main(["resolve", "--root", str(repo), str(repo / "specs" / "example.com.yaml")]) == 0
        parsed = yaml.safe_load(capsys.readouterr().out)
        assert parsed["base_url"] == "https://example.com/"

    def test_it_prints_json_on_request(self, repo, capsys):
        assert (
            main(
                [
                    "resolve",
                    "--root",
                    str(repo),
                    "--json",
                    str(repo / "specs" / "example.com.yaml"),
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out)["spec"] == 1

    def test_it_shows_what_was_inherited(self, repo, capsys):
        (repo / "base" / "engine.yaml").write_text(
            "spec: 1\nrate_limit: 7\nnovel: { cover: { css: img } }\n", encoding="utf-8"
        )
        (repo / "specs" / "child.example.yaml").write_text(
            "spec: 1\nbase_url: https://child.example/\nextends: base/engine.yaml\n",
            encoding="utf-8",
        )
        main(["resolve", "--root", str(repo), str(repo / "specs" / "child.example.yaml")])
        parsed = yaml.safe_load(capsys.readouterr().out)
        assert parsed["rate_limit"] == 7
        assert parsed["novel"]["cover"]["css"] == "img"
        assert "extends" not in parsed

    def test_a_bad_document_reports_rather_than_raising(self, repo, capsys):
        bad = repo / "specs" / "bad.example.yaml"
        bad.write_text("spec: 1\nnovel:\n  title: x\n  title: y\n", encoding="utf-8")
        assert main(["resolve", "--root", str(repo), str(bad)]) == 1
        assert "duplicate key" in capsys.readouterr().err


class TestSchema:
    def test_it_prints_to_stdout(self, capsys):
        assert main(["schema"]) == 0
        assert json.loads(capsys.readouterr().out)["title"] == "Source definition"

    def test_it_writes_a_file(self, tmp_path):
        target = tmp_path / "source.v1.json"
        assert main(["schema", "-o", str(target)]) == 0
        assert json.loads(target.read_text())["x-generator"].startswith("lncrawl-sourcelib==")

    def test_check_fails_when_the_file_is_missing_or_stale(self, tmp_path, capsys):
        target = tmp_path / "source.v1.json"
        assert main(["schema", "-o", str(target), "--check"]) == 1
        target.write_text("{}\n", encoding="utf-8")
        assert main(["schema", "-o", str(target), "--check"]) == 1
        assert "out of date" in capsys.readouterr().err

    def test_check_passes_on_a_freshly_written_file(self, tmp_path):
        target = tmp_path / "source.v1.json"
        main(["schema", "-o", str(target)])
        assert main(["schema", "-o", str(target), "--check"]) == 0
