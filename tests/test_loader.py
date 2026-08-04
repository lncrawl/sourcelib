"""Parsing rules that are easy to get wrong and silent when wrong."""

import pytest
import yaml

from sourcelib.spec.loader import parse_yaml


class TestYaml12Booleans:
    """RFC-0001 specifies YAML 1.2. PyYAML implements 1.1, where these are booleans."""

    @pytest.mark.parametrize("word", ["on", "off", "yes", "no", "y", "n", "Yes", "No", "ON"])
    def test_yaml_11_booleans_stay_strings(self, word):
        assert parse_yaml(f"key: {word}")["key"] == word

    def test_var_on_survives_as_a_key(self):
        # The trap this exists for: `on: url` parses as `True: "url"` under YAML 1.1, so
        # the var loses its scope and reads the novel page instead of the URL string.
        assert parse_yaml("vars:\n  id:\n    on: url\n") == {"vars": {"id": {"on": "url"}}}

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("true", True), ("false", False), ("True", True), ("FALSE", False)],
    )
    def test_yaml_12_booleans_still_parse(self, text, expected):
        assert parse_yaml(f"key: {text}")["key"] is expected

    def test_other_scalars_are_unaffected(self):
        parsed = parse_yaml("i: 3\nf: 1.5\nnull_: null\ns: hello\n")
        assert parsed == {"i": 3, "f": 1.5, "null_": None, "s": "hello"}


class TestDuplicateKeys:
    def test_duplicate_key_is_refused(self):
        # PyYAML keeps the last silently, which in a spec loses a selector with no report.
        with pytest.raises(yaml.YAMLError, match="duplicate key"):
            parse_yaml("novel:\n  title: { css: h1 }\n  title: { css: h2 }\n")

    def test_duplicate_key_reports_its_line(self):
        with pytest.raises(yaml.YAMLError, match="line 3"):
            parse_yaml("a: 1\nb: 2\na: 3\n")

    def test_repeated_key_in_different_mappings_is_fine(self):
        parsed = parse_yaml("novel:\n  title: { css: h1 }\ntoc:\n  items:\n    css: a\n")
        assert parsed["novel"]["title"] == {"css": "h1"}


class TestShape:
    def test_empty_document_is_an_empty_mapping(self):
        assert parse_yaml("") == {}

    def test_a_non_mapping_document_is_refused(self):
        with pytest.raises(ValueError, match="must be a mapping"):
            parse_yaml("- one\n- two\n")
