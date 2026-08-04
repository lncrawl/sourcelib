"""The published JSON Schema.

Editors and models read this, so what matters is that it exposes document keys, refuses
unknown ones, and carries the descriptions that drive autocompletion.
"""

import json

import pytest

from sourcelib.spec.schema import build, render


class TestKeys:
    def test_document_keys_are_exposed_not_private_names(self):
        defs = build()["$defs"]
        assert "while" in defs["Paginate"]["properties"]
        assert "while_" not in defs["Paginate"]["properties"]
        assert "from" in defs["Request"]["properties"]
        assert "from_" not in defs["Request"]["properties"]
        assert "json" in defs["Extractor"]["properties"]
        assert "json_" not in defs["Extractor"]["properties"]

    def test_every_node_refuses_unknown_keys(self):
        # This is what makes an editor flag a typo, and what rejects a trailing underscore.
        for name, definition in build()["$defs"].items():
            if definition.get("type") == "object" or "properties" in definition:
                assert definition.get("additionalProperties") is False, name


class TestDescriptions:
    def test_every_root_field_is_documented(self):
        undocumented = [
            name
            for name, prop in build()["properties"].items()
            if not prop.get("description") and "$ref" not in prop
        ]
        assert undocumented == []

    def test_nested_fields_are_documented(self):
        for name, definition in build()["$defs"].items():
            for field, prop in definition.get("properties", {}).items():
                assert prop.get("description") or "$ref" in prop, f"{name}.{field}"


class TestStability:
    def test_render_is_deterministic(self):
        assert render() == render()

    def test_render_is_valid_json_with_a_trailing_newline(self):
        text = render()
        assert text.endswith("\n")
        assert json.loads(text)["$id"].endswith("source.v1.json")

    def test_it_declares_a_draft_and_a_title(self):
        schema = build()
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["title"] == "Source definition"


class TestGenerator:
    """The specs repository regenerates with this, so it must be installable as written."""

    def test_it_records_a_pip_requirement(self):
        assert build()["x-generator"].startswith("lncrawl-sourcelib==")

    def test_the_version_is_not_the_source_tree_placeholder(self):
        # 0.0.0 means the package metadata was unreadable, which would pin nothing.
        assert not build()["x-generator"].endswith("==0.0.0")

    def test_it_is_an_unknown_keyword_a_validator_ignores(self):
        jsonschema = pytest.importorskip("jsonschema")
        jsonschema.Draft202012Validator.check_schema(build())
