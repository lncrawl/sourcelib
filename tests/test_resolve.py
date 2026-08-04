"""Merging `extends`, per RFC-0001 section 5."""

import pytest

from sourcelib.spec.resolve import MAX_DEPTH, ResolveError, merge, resolve_file


class Repo:
    """A repository laid out the way RFC-0001 section 8.2 requires."""

    def __init__(self, root):
        self.root = root
        for folder in ("specs", "base", "disabled", "hooks"):
            (root / folder).mkdir(exist_ok=True)

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def __truediv__(self, other):
        return self.root / other

    def __fspath__(self):
        return str(self.root)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


class TestMerge:
    def test_a_child_scalar_replaces(self):
        assert merge({"rate_limit": 3.0}, {"rate_limit": 1.0}) == {"rate_limit": 1.0}

    def test_mappings_merge_key_by_key(self):
        parent = {"novel": {"title": {"css": "h1"}, "cover": {"css": "img"}}}
        child = {"novel": {"title": {"css": "h2"}}}
        assert merge(parent, child) == {"novel": {"title": {"css": "h2"}, "cover": {"css": "img"}}}

    def test_an_ordinary_list_is_replaced_not_appended(self):
        # Step order in a pipe is semantic, so appending would run the child's steps in a
        # position it never chose.
        parent = {"pipe": ["trim", "paragraphs"]}
        child = {"pipe": ["paragraphs"]}
        assert merge(parent, child) == {"pipe": ["paragraphs"]}

    def test_fallback_prepends_so_the_child_is_tried_first(self):
        parent = {"fallback": [{"css": ".old"}]}
        child = {"fallback": [{"css": ".new"}]}
        assert merge(parent, child) == {"fallback": [{"css": ".new"}, {"css": ".old"}]}

    def test_an_explicit_null_deletes_the_inherited_key(self):
        assert merge({"search": {"css": "a"}}, {"search": None}) == {}

    def test_a_null_for_a_key_the_parent_lacks_is_kept(self):
        # Nothing to delete, so it stays and the model decides whether it is legal.
        assert merge({}, {"encoding": None}) == {"encoding": None}

    def test_the_parent_is_not_mutated(self):
        parent = {"novel": {"title": {"css": "h1"}}}
        merge(parent, {"novel": {"title": {"css": "h2"}}})
        assert parent == {"novel": {"title": {"css": "h1"}}}


class TestResolveFile:
    def test_a_two_line_alias_inherits_everything(self, repo):
        repo.write(
            "specs/example.com.yaml",
            "spec: 1\n"
            "base_url: https://example.com/\n"
            "novel: { title: { css: h1 } }\n"
            "toc: { request: { page: novel }, items: { css: a } }\n"
            "chapter: { body: { css: '#content' } }\n",
        )
        alias = repo.write(
            "specs/mirror.example.yaml",
            "spec: 1\nbase_url: https://mirror.example/\nextends: specs/example.com.yaml\n",
        )
        spec = resolve_file(alias, root=repo)
        assert spec.base_url == "https://mirror.example/"
        assert spec.novel and spec.novel.title and spec.novel.title.css == "h1"
        assert spec.extends is None

    def test_a_chain_resolves_bottom_up(self, repo):
        repo.write("base/engine.yaml", "spec: 1\nrate_limit: 5\nlanguage: en\n")
        repo.write(
            "base/theme.yaml",
            "spec: 1\nextends: base/engine.yaml\nrate_limit: 2\nparser: html.parser\n",
        )
        leaf = repo.write(
            "specs/site.example.yaml",
            "spec: 1\nbase_url: https://site.example/\nextends: base/theme.yaml\n",
        )
        spec = resolve_file(leaf, root=repo)
        assert spec.rate_limit == 2.0
        assert spec.language == "en"
        assert spec.parser == "html.parser"

    def test_a_missing_parent_is_reported(self, repo):
        leaf = repo.write(
            "specs/site.example.yaml",
            "spec: 1\nbase_url: https://site.example/\nextends: base/absent.yaml\n",
        )
        with pytest.raises(ResolveError, match="does not exist"):
            resolve_file(leaf, root=repo)

    def test_extending_a_disabled_spec_is_refused(self, repo):
        repo.write("disabled/dead.example.yaml", "spec: 1\nbase_url: https://dead.example/\n")
        leaf = repo.write(
            "specs/site.example.yaml",
            "spec: 1\nbase_url: https://site.example/\nextends: disabled/dead.example.yaml\n",
        )
        # Otherwise disabling a spec silently orphans every mirror extending it.
        with pytest.raises(ResolveError, match="must name a document in"):
            resolve_file(leaf, root=repo)

    def test_a_path_escaping_the_repository_is_refused(self, repo):
        leaf = repo.write(
            "specs/site.example.yaml",
            "spec: 1\nbase_url: https://site.example/\nextends: ../../etc/passwd\n",
        )
        with pytest.raises(ResolveError, match="escapes the repository"):
            resolve_file(leaf, root=repo)

    def test_a_cycle_is_reported_rather_than_looping(self, repo):
        repo.write("base/a.yaml", "spec: 1\nextends: base/b.yaml\n")
        repo.write("base/b.yaml", "spec: 1\nextends: base/a.yaml\n")
        leaf = repo.write(
            "specs/site.example.yaml",
            "spec: 1\nbase_url: https://site.example/\nextends: base/a.yaml\n",
        )
        with pytest.raises(ResolveError, match="cycle"):
            resolve_file(leaf, root=repo)

    def test_a_self_reference_is_a_cycle(self, repo):
        leaf = repo.write(
            "specs/site.example.yaml",
            "spec: 1\nbase_url: https://site.example/\nextends: specs/site.example.yaml\n",
        )
        with pytest.raises(ResolveError, match="cycle"):
            resolve_file(leaf, root=repo)

    def test_a_chain_past_the_limit_is_refused(self, repo):
        depth = MAX_DEPTH + 2
        for i in range(depth):
            parent = f"extends: base/link{i + 1}.yaml\n" if i + 1 < depth else ""
            repo.write(f"base/link{i}.yaml", f"spec: 1\n{parent}")
        leaf = repo.write(
            "specs/site.example.yaml",
            "spec: 1\nbase_url: https://site.example/\nextends: base/link0.yaml\n",
        )
        with pytest.raises(ResolveError, match="deeper than"):
            resolve_file(leaf, root=repo)

    def test_a_chain_at_the_limit_is_allowed(self, repo):
        for i in range(MAX_DEPTH):
            parent = f"extends: base/link{i + 1}.yaml\n" if i + 1 < MAX_DEPTH else ""
            repo.write(f"base/link{i}.yaml", f"spec: 1\n{parent}")
        leaf = repo.write(
            "specs/site.example.yaml",
            "spec: 1\nbase_url: https://site.example/\nextends: base/link0.yaml\n",
        )
        assert resolve_file(leaf, root=repo).spec == 1

    def test_a_spec_without_extends_needs_no_root(self, repo):
        leaf = repo.write("specs/site.example.yaml", "spec: 1\nbase_url: https://site.example/\n")
        assert resolve_file(leaf).base_url == "https://site.example/"

    def test_fallback_accumulates_down_a_chain(self, repo):
        repo.write(
            "base/one.yaml",
            "spec: 1\nnovel: { title: { css: h1, fallback: [{ css: .a }] } }\n",
        )
        leaf = repo.write(
            "specs/site.example.yaml",
            "spec: 1\n"
            "base_url: https://site.example/\n"
            "extends: base/one.yaml\n"
            "novel: { title: { fallback: [{ css: .b }] } }\n",
        )
        spec = resolve_file(leaf, root=repo)
        assert spec.novel and spec.novel.title
        assert [f.css for f in spec.novel.title.fallback] == [".b", ".a"]
        assert spec.novel.title.css == "h1"
