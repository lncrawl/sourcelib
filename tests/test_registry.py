"""Host normalisation and spec discovery.

Normalisation is tested hard because the whole layout rests on it deriving the same filename
in every implementation. A disagreement here does not fail loudly; it makes a host
unreachable.
"""

import pytest

from sourcelib.registry import Registry, normalise_host

SERVABLE = (
    "spec: 1\n"
    "base_url: {url}\n"
    "novel: {{ title: {{ css: h1 }} }}\n"
    "toc: {{ request: {{ page: novel }}, items: {{ css: a }} }}\n"
    "chapter: {{ body: {{ css: '#content' }} }}\n"
)


class Repo:
    def __init__(self, root):
        self.root = root
        for folder in ("specs", "disabled", "base"):
            (root / folder).mkdir(exist_ok=True)

    def spec(self, host, url=None, folder="specs", body=None):
        text = body if body is not None else SERVABLE.format(url=url or f"https://{host}/")
        (self.root / folder / f"{host}.yaml").write_text(text, encoding="utf-8")

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


class TestNormaliseHost:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("https://example.com/", "example.com"),
            ("http://example.com", "example.com"),
            ("https://www.example.com/", "example.com"),
            ("example.com", "example.com"),
            ("//example.com", "example.com"),
            ("https://EXAMPLE.COM/", "example.com"),
            ("https://example.com:8443/", "example.com"),
            ("https://user:pass@example.com/", "example.com"),
            ("https://example.com/novel/x", "example.com"),
            ("  https://example.com/  ", "example.com"),
        ],
    )
    def test_it_folds_everything_that_is_not_the_host(self, given, expected):
        assert normalise_host(given) == expected

    def test_scheme_and_www_variants_agree(self):
        # RFC-0001 section 8.1: one document answers for all of these, so they must never
        # become separate specs.
        variants = [
            "http://example.com/",
            "https://example.com/",
            "http://www.example.com/",
            "https://www.example.com/",
        ]
        assert len({normalise_host(v) for v in variants}) == 1

    def test_a_subdomain_is_its_own_host(self):
        # es.mtlnovel.com is a different source from mtlnovel.com, serving another language.
        assert normalise_host("https://es.mtlnovel.com/") == "es.mtlnovel.com"

    def test_www_is_only_stripped_as_a_prefix(self):
        assert normalise_host("https://wwwsomething.com/") == "wwwsomething.com"
        assert normalise_host("https://www2.example.com/") == "www2.example.com"

    def test_an_ipv6_literal_keeps_its_colons(self):
        assert normalise_host("http://[::1]:8080/") == "[::1]"

    def test_an_internationalised_host_becomes_ascii(self):
        assert normalise_host("https://bücher.example/") == "xn--bcher-kva.example"

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            # The standard library's codec is IDNA2003 and maps this to fass.example, a
            # different host. RFC-0001 section 8.1 names ß and ς for exactly this reason.
            ("https://faß.example/", "xn--fa-hia.example"),
            ("https://ς.example/", "xn--3xa.example"),
        ],
    )
    def test_idna2008_is_used_not_the_standard_library_codec(self, given, expected):
        assert normalise_host(given) == expected

    def test_an_unencodable_host_yields_no_match_rather_than_raising(self, tmp_path):
        # A host that cannot name a file is simply unknown; guessing would be worse.
        assert normalise_host("https://" + "ـ.example/") == "ـ.example"

    def test_it_is_idempotent(self):
        once = normalise_host("https://WWW.Example.COM:443/path")
        assert normalise_host(once) == once


class TestRegistry:
    def test_it_indexes_served_and_disabled_separately(self, repo):
        repo.spec("example.com")
        repo.spec(
            "dead.example",
            folder="disabled",
            body="spec: 1\nbase_url: https://dead.example/\ndisabled: Domain expired\n",
        )
        registry = Registry.load(repo.root)
        assert registry.hosts == ["dead.example", "example.com"]
        assert [e.host for e in registry.served] == ["example.com"]

    def test_a_url_finds_its_spec_however_it_is_written(self, repo):
        repo.spec("example.com")
        registry = Registry.load(repo.root)
        for url in (
            "https://example.com/",
            "http://www.example.com/novel/thing",
            "example.com",
        ):
            entry = registry.find(url)
            assert entry is not None and entry.host == "example.com"

    def test_a_disabled_host_is_an_answer_not_a_miss(self, repo):
        repo.spec(
            "dead.example",
            folder="disabled",
            body="spec: 1\nbase_url: https://dead.example/\ndisabled: Site is down\n",
        )
        registry = Registry.load(repo.root)
        entry = registry.find("https://dead.example/x")
        # Returning None here is what would let a turned-off host fall through to something
        # else, which is the trap RFC-0001 section 9.3 names.
        assert entry is not None
        assert entry.served is False
        assert entry.disabled_reason == "Site is down"
        assert registry.serves("https://dead.example/") is False

    def test_an_unknown_host_is_none(self, repo):
        repo.spec("example.com")
        registry = Registry.load(repo.root)
        assert registry.find("https://nobody.example/") is None
        assert registry.serves("https://nobody.example/") is False

    def test_abstract_specs_are_not_registered(self, repo):
        repo.spec("example.com")
        repo.write("base/engine.yaml", "spec: 1\nrate_limit: 2\n")
        registry = Registry.load(repo.root)
        # A base has no base_url, so it can never be mistaken for a source.
        assert registry.hosts == ["example.com"]

    def test_an_alias_resolves_through_its_parent(self, repo):
        repo.spec("example.com")
        repo.spec(
            "mirror.example",
            body="spec: 1\nbase_url: https://mirror.example/\nextends: specs/example.com.yaml\n",
        )
        registry = Registry.load(repo.root)
        entry = registry.find("https://mirror.example/")
        assert entry is not None
        assert entry.spec.novel and entry.spec.novel.title
        assert entry.spec.novel.title.css == "h1"

    def test_a_filename_disagreeing_with_base_url_is_reported(self, repo):
        repo.spec("wrong.example", url="https://right.example/")
        registry = Registry.load(repo.root)
        assert registry.hosts == []
        assert "declares base_url for" in registry.problems[0][1]

    def test_one_broken_document_does_not_deny_the_others(self, repo):
        repo.spec("good.example")
        repo.write("specs/bad.example.yaml", "spec: 1\nbase_url: https://bad.example/\nnvoel: {}\n")
        registry = Registry.load(repo.root)
        assert registry.hosts == ["good.example"]
        assert len(registry.problems) == 1

    def test_strict_raises_instead_of_recording(self, repo):
        repo.write("specs/bad.example.yaml", "spec: 1\nbase_url: https://bad.example/\nnvoel: {}\n")
        with pytest.raises(ValueError):
            Registry.load(repo.root, strict=True)

    def test_a_concrete_folder_document_needs_a_base_url(self, repo):
        repo.write("specs/nameless.yaml", "spec: 1\nnovel: { title: { css: h1 } }\n")
        registry = Registry.load(repo.root)
        assert "declares no base_url" in registry.problems[0][1]

    def test_it_reports_specs_that_cannot_serve(self, repo):
        repo.spec("example.com")
        repo.spec(
            "partial.example",
            body="spec: 1\nbase_url: https://partial.example/\nnovel: {}\n",
        )
        registry = Registry.load(repo.root)
        unservable = dict((entry.host, problems) for entry, problems in registry.unservable())
        assert list(unservable) == ["partial.example"]
        assert {p.field for p in unservable["partial.example"]} == {"toc.items", "chapter.body"}

    def test_len_and_contains(self, repo):
        repo.spec("example.com")
        registry = Registry.load(repo.root)
        assert len(registry) == 1
        assert "https://www.example.com/" in registry
        assert "https://other.example/" not in registry
        assert 42 not in registry
