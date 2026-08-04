"""Hooks taking effect during a crawl, per RFC-0001 section 7.

The shape here is the hostile end of the corpus: a body the site encrypts, a title the site
lies about, and a table of contents that is not a document at all. Each is one small function
rather than a Python class replacing the interpreter.
"""

import base64

import pytest

from sourcelib.fetch import RecordedFetcher
from sourcelib.runtime import CrawlError, Interpreter
from sourcelib.spec.extract import Document
from sourcelib.spec.model import SourceSpec

BODY_HOOK = '''
"""One host's hooks, sharing the decoding it needs."""

import base64


def _decode(text):
    return base64.b64decode(text.strip()).decode("utf-8")


def chapter_body(value, doc, ctx):
    node = doc.node.select_one("#cipher")
    seen = ctx.state.update_atomically(ctx.key("count"), lambda n: (n or 0) + 1)
    ctx.state[ctx.key("last")] = seen
    return _decode(node.get_text())


def novel_title(value, doc, ctx):
    return f"{value} (fixed)"
'''

TOC_HOOK = '''
"""A table of contents that is not a document: rows built from an API shape."""


def toc_items(value, doc, ctx):
    return [
        {"title": f"Ch {n}", "url": f"https://e.test/c/{n}", "volume": 1 if n < 3 else 2}
        for n in (1, 2, 3)
    ]


def toc_volumes(value, doc, ctx):
    return [{"title": "Part One"}, {"title": "Part Two"}]
'''


def encoded(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


NOVEL = """<html><body>
  <h1>Real Title</h1>
  <ul class="list"><li><a href="/c/1">Ch 1</a></li></ul>
</body></html>"""

CHAPTER = f'<html><body><div id="cipher">{encoded("<p>Plain text.</p>")}</div></body></html>'


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "hooks" / "sites").mkdir(parents=True)
    (tmp_path / "hooks" / "sites" / "e.test.py").write_text(BODY_HOOK, encoding="utf-8")
    (tmp_path / "hooks" / "sites" / "api.test.py").write_text(TOC_HOOK, encoding="utf-8")
    return tmp_path


@pytest.fixture
def site():
    return RecordedFetcher(
        {
            "https://e.test/novel/x": NOVEL,
            "https://e.test/c/1": CHAPTER,
            "https://e.test/c/2": CHAPTER,
            "https://e.test/c/3": CHAPTER,
        }
    )


BASE = {
    "spec": 1,
    "base_url": "https://e.test/",
    "novel": {"title": {"css": "h1"}},
    "toc": {
        "request": {"page": "novel"},
        "items": {
            "css": "ul.list li",
            "fields": {"title": {"css": "a"}, "url": {"css": "a", "attr": "href"}},
        },
    },
    "chapter": {"body": {"css": "#cipher"}},
}


class TestBodyHook:
    def test_the_hook_replaces_what_the_spec_extracted(self, repo, site):
        spec = SourceSpec.model_validate(
            {**BASE, "hooks": {"chapter.body": "hooks/sites/e.test.py"}}
        )
        interpreter = Interpreter.load(spec, site, root=repo)
        novel = interpreter.read_novel("https://e.test/novel/x")
        chapter = interpreter.download_chapter(novel, novel.chapters[0])
        assert chapter.body == "<p>Plain text.</p>"
        assert chapter.success is True

    def test_a_field_hook_sees_what_the_spec_produced(self, repo, site):
        spec = SourceSpec.model_validate(
            {**BASE, "hooks": {"novel.title": "hooks/sites/e.test.py"}}
        )
        novel = Interpreter.load(spec, site, root=repo).read_novel("https://e.test/novel/x")
        # The hook runs last, over the extracted value rather than instead of it.
        assert novel.title == "Real Title (fixed)"

    def test_state_is_namespaced_by_the_hook_file(self, repo, site):
        spec = SourceSpec.model_validate(
            {**BASE, "hooks": {"chapter.body": "hooks/sites/e.test.py"}}
        )
        interpreter = Interpreter.load(spec, site, root=repo)
        novel = interpreter.read_novel("https://e.test/novel/x")
        interpreter.download_chapter(novel, novel.chapters[0])
        assert interpreter.ctx.state["e.test/count"] == 1

    def test_state_accumulates_across_chapters_of_one_crawl(self, repo, site):
        toc = {
            "request": {"page": "novel"},
            "items": {
                "css": "ul.list li",
                "fields": {"title": {"css": "a"}, "url": {"const": "https://e.test/c/1"}},
            },
        }
        spec = SourceSpec.model_validate(
            {**BASE, "toc": toc, "hooks": {"chapter.body": "hooks/sites/e.test.py"}}
        )
        interpreter = Interpreter.load(spec, site, root=repo)
        novel = interpreter.read_novel("https://e.test/novel/x")
        interpreter.download_chapter(novel, novel.chapters[0])
        interpreter.download_chapter(novel, novel.chapters[0])
        assert interpreter.ctx.state["e.test/count"] == 2

    def test_two_interpreters_do_not_share_state(self, repo, site):
        spec = SourceSpec.model_validate(
            {**BASE, "hooks": {"chapter.body": "hooks/sites/e.test.py"}}
        )
        first = Interpreter.load(spec, site, root=repo)
        second = Interpreter.load(spec, site, root=repo)
        novel = first.read_novel("https://e.test/novel/x")
        first.download_chapter(novel, novel.chapters[0])
        # One Context per crawl. A module-level global here would hand one novel another's
        # credentials once two run at once.
        assert "e.test/count" not in second.ctx.state


class TestHookedToc:
    def test_a_hook_can_supply_the_whole_chapter_list(self, repo, site):
        spec = SourceSpec.model_validate(
            {
                "spec": 1,
                "base_url": "https://e.test/",
                "novel": {"title": {"css": "h1"}},
                "toc": {"request": {"page": "novel"}},
                "chapter": {"body": {"css": "#cipher"}},
                "hooks": {"toc.items": "hooks/sites/api.test.py"},
            }
        )
        novel = Interpreter.load(spec, site, root=repo).read_novel("https://e.test/novel/x")
        assert [c.title for c in novel.chapters] == ["Ch 1", "Ch 2", "Ch 3"]
        assert [c.id for c in novel.chapters] == [1, 2, 3]

    def test_a_hook_can_supply_volumes_too(self, repo, site):
        # This was recorded as a gap: a hook-driven list had no way to produce volumes. The
        # derived point set gives it toc.volumes.
        spec = SourceSpec.model_validate(
            {
                "spec": 1,
                "base_url": "https://e.test/",
                "novel": {"title": {"css": "h1"}},
                "toc": {"request": {"page": "novel"}},
                "chapter": {"body": {"css": "#cipher"}},
                "hooks": {
                    "toc.items": "hooks/sites/api.test.py",
                    "toc.volumes": "hooks/sites/api.test.py",
                },
            }
        )
        novel = Interpreter.load(spec, site, root=repo).read_novel("https://e.test/novel/x")
        assert [(v.id, v.title) for v in novel.volumes] == [(1, "Part One"), (2, "Part Two")]
        assert [c.volume for c in novel.chapters] == [1, 1, 2]

    def test_a_hook_producing_nothing_is_an_error_naming_the_point(self, repo, site):
        (repo / "hooks" / "sites" / "empty.test.py").write_text(
            "def toc_items(value, doc, ctx):\n    return []\n", encoding="utf-8"
        )
        spec = SourceSpec.model_validate(
            {
                "spec": 1,
                "base_url": "https://e.test/",
                "novel": {"title": {"css": "h1"}},
                "toc": {"request": {"page": "novel"}},
                "chapter": {"body": {"css": "#cipher"}},
                "hooks": {"toc.items": "hooks/sites/empty.test.py"},
            }
        )
        with pytest.raises(CrawlError) as caught:
            Interpreter.load(spec, site, root=repo).read_novel("https://e.test/novel/x")
        assert caught.value.field == "toc.items"


class TestRequestHook:
    def test_a_request_hook_replaces_the_whole_fetch(self, repo, site):
        # The toc reuses this document via `page: novel`, so the hook must produce a whole
        # page rather than only the field it cares about.
        (repo / "hooks" / "sites" / "grpc.test.py").write_text(
            "from sourcelib.spec.extract import Document\n\n\n"
            "PAGE = (\n"
            '    "<h1>From a protocol</h1>"\n'
            "    \"<ul class='list'><li><a href='/c/1'>Ch 1</a></li></ul>\"\n"
            ")\n\n\n"
            "def novel_request(url, ctx):\n"
            "    return Document.from_html(PAGE, url=url)\n",
            encoding="utf-8",
        )
        spec = SourceSpec.model_validate(
            {**BASE, "hooks": {"novel.request": "hooks/sites/grpc.test.py"}}
        )
        interpreter = Interpreter.load(spec, site, root=repo)
        novel = interpreter.read_novel("https://e.test/novel/x")
        assert novel.title == "From a protocol"
        # The spec's own novel fetch never happened.
        assert ("GET", "https://e.test/novel/x") not in site.calls


class TestNoHooks:
    def test_a_spec_with_no_hooks_needs_no_root(self, site):
        spec = SourceSpec.model_validate(BASE)
        novel = Interpreter.load(spec, site).read_novel("https://e.test/novel/x")
        assert novel.title == "Real Title"

    def test_hooks_declared_without_a_root_are_simply_not_bound(self, site):
        spec = SourceSpec.model_validate(
            {**BASE, "hooks": {"novel.title": "hooks/sites/e.test.py"}}
        )
        novel = Interpreter.load(spec, site).read_novel("https://e.test/novel/x")
        assert novel.title == "Real Title"


class TestContextContents:
    def test_a_hook_reaches_the_session_spec_and_vars(self, repo, site):
        (repo / "hooks" / "sites" / "peek.test.py").write_text(
            "def novel_title(value, doc, ctx):\n"
            "    return f'{ctx.spec.base_url}|{ctx.vars[\"nid\"]}|{bool(ctx.session)}'\n",
            encoding="utf-8",
        )
        spec = SourceSpec.model_validate(
            {
                **BASE,
                "vars": {"nid": {"on": "url", "regex": r"/novel/(\w+)"}},
                "hooks": {"novel.title": "hooks/sites/peek.test.py"},
            }
        )
        novel = Interpreter.load(spec, site, root=repo).read_novel("https://e.test/novel/x")
        assert novel.title == "https://e.test/|x|True"

    def test_the_document_in_scope_reaches_the_hook(self, repo, site):
        spec = SourceSpec.model_validate(
            {**BASE, "hooks": {"chapter.body": "hooks/sites/e.test.py"}}
        )
        interpreter = Interpreter.load(spec, site, root=repo)
        novel = interpreter.read_novel("https://e.test/novel/x")
        chapter = interpreter.download_chapter(novel, novel.chapters[0])
        # The hook selected #cipher out of the document it was handed.
        assert chapter.body == "<p>Plain text.</p>"

    def test_a_document_is_what_the_hook_receives(self):
        assert isinstance(Document.from_html("<p/>"), Document)
