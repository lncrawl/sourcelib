"""The escape hatch, per RFC-0001 sections 7 and 10."""

import threading

import pytest

from sourcelib.hooks import Context, HookError, HookRegistry, State, function_name

SITE_HOOK = '''
"""Hooks for one host, grouped so they share their setup."""


def chapter_body(value, doc, ctx):
    ctx.state[ctx.key("calls")] = ctx.state.get(ctx.key("calls"), 0) + 1
    return f"decrypted:{value}"


def novel_title(value, doc, ctx):
    return "hooked title"


def check_response(response, body):
    return "refused" if "requireTurnstile" in body else None
'''

LIB_HOOK = """
def decode(text):
    return text[::-1]
"""

SHARED_HOOK = """
from hooks.lib.helpers import decode


def chapter_body(value, doc, ctx):
    return decode(value)
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "hooks" / "sites").mkdir(parents=True)
    (tmp_path / "hooks" / "shared").mkdir(parents=True)
    (tmp_path / "hooks" / "lib").mkdir(parents=True)
    (tmp_path / "hooks" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "hooks" / "lib" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "hooks" / "sites" / "wtr-lab.com.py").write_text(SITE_HOOK, encoding="utf-8")
    (tmp_path / "hooks" / "lib" / "helpers.py").write_text(LIB_HOOK, encoding="utf-8")
    (tmp_path / "hooks" / "shared" / "reversed_body.py").write_text(SHARED_HOOK, encoding="utf-8")
    return tmp_path


class TestFunctionName:
    @pytest.mark.parametrize(
        ("point", "expected"),
        [
            ("chapter.body", "chapter_body"),
            ("novel.synopsis", "novel_synopsis"),
            ("toc.volumes", "toc_volumes"),
            ("login", "login"),
            ("check_response", "check_response"),
        ],
    )
    def test_a_dotted_point_becomes_an_underscored_function(self, point, expected):
        assert function_name(point) == expected


class TestLoading:
    def test_a_site_file_loads_by_path_despite_its_name(self, repo):
        # wtr-lab.com.py has a hyphen and dots, so it can never be imported by name.
        module = HookRegistry(repo).load("hooks/sites/wtr-lab.com.py")
        assert callable(module.chapter_body)

    def test_a_file_is_loaded_once(self, repo):
        registry = HookRegistry(repo)
        assert registry.load("hooks/sites/wtr-lab.com.py") is registry.load(
            "hooks/sites/wtr-lab.com.py"
        )

    def test_a_shared_hook_may_import_from_lib(self, repo):
        module = HookRegistry(repo).load("hooks/shared/reversed_body.py")
        assert module.chapter_body("abc", None, None) == "cba"

    def test_a_missing_file_is_reported(self, repo):
        with pytest.raises(HookError, match="does not exist"):
            HookRegistry(repo).load("hooks/sites/absent.py")

    def test_a_path_escaping_the_repository_is_refused(self, repo):
        with pytest.raises(HookError, match="escapes the repository"):
            HookRegistry(repo).load("../../etc/passwd")

    def test_a_non_python_path_is_refused(self, repo):
        (repo / "hooks" / "sites" / "notes.txt").write_text("x", encoding="utf-8")
        with pytest.raises(HookError, match="not a Python file"):
            HookRegistry(repo).load("hooks/sites/notes.txt")

    def test_a_file_that_fails_to_import_is_reported(self, repo):
        (repo / "hooks" / "sites" / "broken.py").write_text("1 / 0\n", encoding="utf-8")
        with pytest.raises(HookError, match="failed to import"):
            HookRegistry(repo).load("hooks/sites/broken.py")


class TestForbiddenImports:
    def test_importing_another_hosts_file_is_refused(self, repo):
        (repo / "hooks" / "shared" / "nosy.py").write_text(
            "from hooks.sites import something\n", encoding="utf-8"
        )
        with pytest.raises(HookError, match="couples two sources"):
            HookRegistry(repo).load("hooks/shared/nosy.py")

    def test_a_plain_import_is_caught_too(self, repo):
        (repo / "hooks" / "shared" / "nosy2.py").write_text(
            "import hooks.sites.other\n", encoding="utf-8"
        )
        with pytest.raises(HookError, match="couples two sources"):
            HookRegistry(repo).load("hooks/shared/nosy2.py")

    def test_the_check_runs_before_the_module_executes(self, repo):
        # Checking afterwards would mean the forbidden import already happened.
        marker = repo / "ran.txt"
        (repo / "hooks" / "shared" / "eager.py").write_text(
            f"open({str(marker)!r}, 'w').write('x')\nimport hooks.sites.other\n",
            encoding="utf-8",
        )
        with pytest.raises(HookError, match="couples two sources"):
            HookRegistry(repo).load("hooks/shared/eager.py")
        assert not marker.exists()

    def test_a_mention_in_a_comment_is_not_a_false_positive(self, repo):
        (repo / "hooks" / "shared" / "fine.py").write_text(
            "# never import from hooks.sites here\nVALUE = 'hooks.sites'\n", encoding="utf-8"
        )
        assert HookRegistry(repo).load("hooks/shared/fine.py").VALUE == "hooks.sites"


class TestBinding:
    def test_a_bare_path_binds_every_point_the_file_defines(self, repo):
        bound = HookRegistry(repo).bind("hooks/sites/wtr-lab.com.py")
        assert set(bound) == {"chapter.body", "novel.title", "check_response"}

    def test_the_owner_is_the_file_stem(self, repo):
        bound = HookRegistry(repo).bind("hooks/sites/wtr-lab.com.py")
        assert bound["chapter.body"][1] == "wtr-lab.com"

    def test_points_may_be_named_individually(self, repo):
        bound = HookRegistry(repo).bind({"chapter.body": "hooks/sites/wtr-lab.com.py"})
        assert set(bound) == {"chapter.body"}

    def test_binding_a_point_the_file_does_not_define_is_reported(self, repo):
        with pytest.raises(HookError, match="does not define toc_items"):
            HookRegistry(repo).bind({"toc.items": "hooks/sites/wtr-lab.com.py"})

    def test_an_unknown_point_is_refused(self, repo):
        with pytest.raises(HookError, match="unknown hook point"):
            HookRegistry(repo).bind({"chapter_body": "hooks/sites/wtr-lab.com.py"})

    def test_a_file_defining_no_points_says_how_to_name_them(self, repo):
        (repo / "hooks" / "sites" / "empty.py").write_text("X = 1\n", encoding="utf-8")
        with pytest.raises(HookError, match="Name each function"):
            HookRegistry(repo).bind("hooks/sites/empty.py")

    def test_no_hooks_binds_nothing(self, repo):
        assert HookRegistry(repo).bind({}) == {}


class TestState:
    def test_it_reads_and_writes(self):
        state = State()
        state["a"] = 1
        assert state["a"] == 1 and "a" in state and len(state) == 1

    def test_it_accepts_values_that_cannot_be_serialized(self):
        # A hook speaking a binary protocol keeps a live client in here.
        state = State()
        state["client"] = threading.Lock()
        assert state["client"] is not None

    def test_setdefault_lets_two_threads_share_one_object(self):
        state = State()
        created = []

        def make():
            value = state.setdefault("client", object())
            created.append(id(value))

        threads = [threading.Thread(target=make) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(set(created)) == 1

    def test_atomic_update_survives_concurrent_writers(self):
        # A hook doing read-modify-write across two calls loses this race.
        state = State()
        barrier = threading.Barrier(16)

        def bump():
            barrier.wait()
            state.update_atomically("count", lambda current: (current or 0) + 1)

        threads = [threading.Thread(target=bump) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert state["count"] == 16

    def test_deleting_and_iterating(self):
        state = State()
        state["a"] = 1
        state["b"] = 2
        assert sorted(state) == ["a", "b"]
        del state["a"]
        assert "a" not in state

    def test_a_snapshot_does_not_alias_the_live_mapping(self):
        state = State()
        state["a"] = 1
        snapshot = state.snapshot()
        state["a"] = 2
        assert snapshot["a"] == 1

    def test_get_returns_a_default(self):
        assert State().get("absent", "fallback") == "fallback"


class TestContext:
    def test_it_carries_what_section_7_2_requires(self):
        context = Context(session="s", spec="spec", variables={"a": 1})
        assert context.session == "s"
        assert context.spec == "spec"
        assert context.vars == {"a": 1}
        assert isinstance(context.state, State)

    def test_key_namespaces_by_the_calling_hook(self):
        # Two independent hooks reaching for state["token"] is a collision between files that
        # have never seen each other.
        context = Context("s", "spec", {}, owner="wtr-lab.com")
        assert context.key("bearer") == "wtr-lab.com/bearer"

    def test_key_is_a_no_op_without_an_owner(self):
        assert Context("s", "spec", {}).key("bearer") == "bearer"

    def test_for_owner_shares_the_state_but_relabels(self):
        first = Context("s", "spec", {}, owner="a")
        second = first.for_owner("b")
        first.state["shared"] = 1
        assert second.state["shared"] == 1
        assert second.owner == "b"
        assert second.key("x") == "b/x"


class TestHooksInUse:
    def test_a_hook_reads_and_writes_its_own_state(self, repo):
        bound = HookRegistry(repo).bind("hooks/sites/wtr-lab.com.py")
        function, owner = bound["chapter.body"]
        context = Context("session", "spec", {}, owner=owner)

        assert function("cipher", None, context) == "decrypted:cipher"
        function("cipher", None, context)
        assert context.state["wtr-lab.com/calls"] == 2

    def test_check_response_keeps_its_own_shape(self, repo):
        function, _ = HookRegistry(repo).bind("hooks/sites/wtr-lab.com.py")["check_response"]
        assert function(None, '{"requireTurnstile": true}') == "refused"
        assert function(None, "<html/>") is None

    def test_two_crawls_do_not_share_state(self, repo):
        bound = HookRegistry(repo).bind("hooks/sites/wtr-lab.com.py")
        function, owner = bound["chapter.body"]
        first = Context("s", "spec", {}, owner=owner)
        second = Context("s", "spec", {}, owner=owner)
        function("a", None, first)
        # One Context per crawl, so nothing the first wrote reaches the second. A module-level
        # global would hand one novel another's credentials under load.
        assert "wtr-lab.com/calls" not in second.state
