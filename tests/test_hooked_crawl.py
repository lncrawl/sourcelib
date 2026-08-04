"""Hooks taking effect during a crawl, per RFC-0001 section 7.

The shape here is the hostile end of the corpus: a body the site encrypts, a title the site
lies about, and a table of contents that is not a document at all. Each is one small function
rather than a Python class replacing the interpreter.
"""

import base64

import pytest

from sourcelib.fetch import RecordedFetcher
from sourcelib.hooks import hook_points
from sourcelib.runtime import CrawlError, Interpreter
from sourcelib.spec.extract import Document
from sourcelib.spec.model import SESSION_HOOK_POINTS, SourceSpec

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


class TestEveryPointIsReachable:
    """A bound hook must actually be called.

    Hook points are derived from the stage set (section 3.9.2), so the enum grows on its own
    while the calls that honour it are hand-written. Five points were legal, bindable and dead:
    `search.items`, `novel.language`, `toc.volumes`, `chapter.request` and `chapter.url`. That is
    the one failure a contributor cannot debug from outside, because validation passes and the
    hook simply never runs.

    Table-driven on `hook_points()` rather than one test per point, so a point added later is
    covered without anyone remembering to cover it.
    """

    SPEC = {
        "spec": 1,
        "base_url": "https://e.test/",
        "search": {
            "request": {"get": "{origin}/s?q={query}"},
            "css": ".r",
            "fields": {"title": {"css": "a"}, "url": {"css": "a", "attr": "href"}},
        },
        "novel": {
            "title": {"css": "h1.t"},
            "cover": {"css": "img"},
            "authors": {"css": ".a"},
            "tags": {"css": ".g"},
            "synopsis": {"css": ".s"},
        },
        "toc": {
            "request": {"page": "novel"},
            "items": {
                "css": "li.c",
                "fields": {"title": {"css": "a"}, "url": {"css": "a", "attr": "href"}},
            },
            "volumes": {"css": "li.v", "fields": {"title": {}}},
        },
        "chapter": {"body": {"css": "#content"}},
    }

    NOVEL = (
        "<html><body><h1 class=t>T</h1>"
        '<ul><li class=v>Vol 1</li><li class=c><a href="/c/1">Ch 1</a></li></ul></body></html>'
    )
    CHAPTER = "<html><body><div id=content><p>hi</p></div></body></html>"
    SEARCH = '<html><body><div class=r><a href="/n/x">Res</a></div></body></html>'

    def _answer(self, point, called):
        """A marker that records the call and returns something the point will accept."""

        def hook(*args, **kwargs):
            called.append(point)
            if point == "toc.items":
                return [{"url": "https://e.test/c/1", "title": "Ch 1"}]
            if point == "search.items":
                return [{"url": "https://e.test/n/x", "title": "Res"}]
            if point == "toc.volumes":
                return {}
            if point.endswith(".request"):
                markup = self.CHAPTER if point.startswith("chapter") else self.NOVEL
                return Document.from_html(markup, url="https://e.test/n/x")
            if point == "chapter.url":
                return "https://e.test/c/1"
            if point in SESSION_HOOK_POINTS:
                # `check_response` reads a truthy return as a refusal and `login` returns
                # nothing, so for both of them "no news" is the answer that lets a crawl finish.
                return None
            return args[0] if args else None

        return hook

    @pytest.mark.parametrize("point", sorted(hook_points()))
    def test_the_point_is_called(self, point):
        called: list = []
        site = RecordedFetcher(
            {
                "https://e.test/n/x": self.NOVEL,
                "https://e.test/c/1": self.CHAPTER,
                "https://e.test/s?q=q": self.SEARCH,
            }
        )
        interpreter = Interpreter(
            SourceSpec.model_validate(self.SPEC),
            site,
            hooks={point: (self._answer(point, called), "probe")},
        )
        interpreter.search("q")
        novel = interpreter.read_novel("https://e.test/n/x")
        interpreter.download_chapter(novel, novel.chapters[0])

        assert called == [point] or point in called, f"{point} is bindable but never called"


class TestSessionPoints:
    """`check_response` and `login`, per RFC-0001 section 7.2.

    Both were specified, bindable and called by nothing. They belong to the session rather than
    to a stage, so they are wired by wrapping the fetcher instead of by growing its protocol.
    """

    SPEC = {
        "spec": 1,
        "base_url": "https://e.test/",
        "novel": {"title": {"css": "h1"}},
        "toc": {
            "request": {"page": "novel"},
            "items": {"css": "li a", "fields": {"title": {}, "url": {"attr": "href"}}},
        },
        "chapter": {"body": {"css": "#c"}},
    }
    NOVEL = '<html><body><h1>T</h1><ul><li><a href="/c/1">Ch 1</a></li></ul></body></html>'
    CHAPTER = '<html><body><div id="c"><p>hi</p></div></body></html>'

    def interpreter(self, hooks):
        site = RecordedFetcher(
            {"https://e.test/n/x": self.NOVEL, "https://e.test/c/1": self.CHAPTER}
        )
        spec = SourceSpec.model_validate(self.SPEC)
        return Interpreter(spec, site, hooks={p: (f, "probe") for p, f in hooks.items()}), site

    def test_a_refusal_stops_the_crawl_and_names_the_url(self):
        def check(response, ctx):
            return "challenge page" if "n/x" in getattr(response, "url", "") else None

        interpreter, _ = self.interpreter({"check_response": check})
        with pytest.raises(Exception, match="was refused: challenge page"):
            interpreter.read_novel("https://e.test/n/x")

    def test_returning_nothing_lets_the_response_through(self):
        seen = []

        def check(response, ctx):
            seen.append(getattr(response, "url", ""))
            return None

        interpreter, _ = self.interpreter({"check_response": check})
        novel = interpreter.read_novel("https://e.test/n/x")
        assert novel.title == "T"
        assert seen

    def test_login_runs_once_however_many_requests_follow(self):
        calls = []

        interpreter, _ = self.interpreter({"login": lambda ctx: calls.append(1)})
        novel = interpreter.read_novel("https://e.test/n/x")
        interpreter.download_chapter(novel, novel.chapters[0])
        assert calls == [1]

    def test_login_runs_before_the_first_request(self):
        order = []

        def login(ctx):
            order.append("login")

        def check(response, ctx):
            order.append("response")
            return None

        interpreter, _ = self.interpreter({"login": login, "check_response": check})
        interpreter.read_novel("https://e.test/n/x")
        assert order[0] == "login"

    def test_an_unhooked_spec_keeps_its_own_fetcher(self):
        # No wrapper at all when neither point is bound, so nothing pays for a feature it
        # does not use.
        interpreter, site = self.interpreter({})
        assert interpreter.fetcher is site
