import textwrap

import schema

import tsrc.config

import pytest
import mock


def test_invalid_syntax(tmp_path):
    foo_yml = tmp_path.joinpath("foo.yml")
    foo_yml.write_text(textwrap.dedent(
        """
        foo:
          bar:
            baz: [

        baz: 42
        """))
    with pytest.raises(tsrc.InvalidConfig) as e:
        tsrc.config.parse_config(foo_yml, schema=None)
    assert e.value.path == foo_yml
    assert "flow sequence" in e.value.details
    assert "ligne 3, col 9" in e.value.details


def test_invalid_schema(tmp_path):
    foo_yml = tmp_path.joinpath("foo.yml")
    foo_yml.write_text(textwrap.dedent(
        """
        foo:
            bar: 42
        """
    ))
    foo_schema = schema.Schema(
        {"foo": {"bar": str}}
    )
    with pytest.raises(tsrc.InvalidConfig) as e:
        tsrc.config.parse_config(foo_yml, schema=foo_schema)
    assert "42 should be instance of 'str'" in e.value.details
