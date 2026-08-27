"""Build engine-shaped introspection results from SDL for the codegen tests.

The engine's introspection is not the standard one: every type, field,
argument, input field and enum value carries a ``directives`` list, and the
symbols a module contributes are marked ``@sourceMap(module: "<name>")``.
"""

import json
from collections.abc import Callable
from typing import Any

import graphql
import pytest

Owners = dict[str | tuple[str, str], str]


def _source_map(module: str) -> dict[str, Any]:
    return {
        "name": "sourceMap",
        "args": [{"name": "module", "value": json.dumps(module)}],
    }


def _with_directives(node: dict[str, Any], directives: list[dict[str, Any]]) -> None:
    node["directives"] = directives


def introspect(sdl: str, owners: Owners | None = None) -> dict[str, Any]:
    """Introspect an SDL schema the way the engine does.

    ``owners`` maps a type name, or a ``(type, field)`` pair, to the module that
    contributes it. Everything else is core.
    """
    owners = owners or {}
    schema = graphql.build_schema(sdl)
    result: dict[str, Any] = dict(graphql.introspection_from_schema(schema))
    for type_ in result["__schema"]["types"]:
        name = type_["name"]
        _with_directives(type_, [_source_map(owners[name])] if name in owners else [])
        for field_ in type_.get("fields") or ():
            key = (name, field_["name"])
            _with_directives(
                field_, [_source_map(owners[key])] if key in owners else []
            )
            for arg in field_.get("args") or ():
                _with_directives(arg, [])
        for input_field in type_.get("inputFields") or ():
            _with_directives(input_field, [])
        for value in type_.get("enumValues") or ():
            _with_directives(value, [])
    result["__schemaVersion"] = "v1.0.0"
    return result


@pytest.fixture
def introspection() -> Callable[..., dict[str, Any]]:
    return introspect
