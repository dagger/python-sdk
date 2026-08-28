"""Attribute schema symbols to the module that contributed them.

The engine annotates every type and field it installs on behalf of a module
with ``@sourceMap(module: "<name>")``; core symbols carry no ``module``. That
directive is the partition between the core API and each module's client.
"""

import copy
import keyword
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, TypeAlias

import graphql

Introspection: TypeAlias = dict[str, Any]
TypeName: TypeAlias = str
FieldName: TypeAlias = str
ModuleName: TypeAlias = str

SOURCE_MAP_DIRECTIVE = "sourceMap"
QUERY_TYPE = "Query"

# Directives are only read off kinds the engine can attribute to a module.
_ATTRIBUTABLE_KINDS = frozenset(
    {"OBJECT", "INTERFACE", "INPUT_OBJECT", "ENUM", "SCALAR"}
)


class PartitionError(ValueError):
    """The schema cannot be split into core and module-owned symbols."""


def owner_of(directives: list[dict[str, Any]] | None) -> ModuleName:
    """The module named by a ``@sourceMap`` directive, or "" for core."""
    for directive in directives or ():
        if directive["name"] != SOURCE_MAP_DIRECTIVE:
            continue
        for arg in directive["args"]:
            if arg["name"] == "module":
                value = graphql.value_from_ast_untyped(
                    graphql.parse_const_value(arg["value"])
                )
                return str(value or "")
    return ""


@dataclass(frozen=True)
class Ownership:
    """Which module owns each non-core type and each non-core field on a core type."""

    types: dict[TypeName, ModuleName] = field(default_factory=dict)
    fields: dict[tuple[TypeName, FieldName], ModuleName] = field(default_factory=dict)

    @property
    def modules(self) -> frozenset[ModuleName]:
        return frozenset(self.types.values()) | frozenset(self.fields.values())

    def owned_types(self, module: ModuleName) -> frozenset[TypeName]:
        return frozenset(n for n, m in self.types.items() if m == module)

    def owned_fields(self, module: ModuleName) -> frozenset[tuple[TypeName, FieldName]]:
        return frozenset(k for k, m in self.fields.items() if m == module)


def ownership(schema: Introspection) -> Ownership:
    """Read the partition off an introspection result's ``__schema``."""
    types: dict[TypeName, ModuleName] = {}
    fields: dict[tuple[TypeName, FieldName], ModuleName] = {}
    for type_ in schema["types"]:
        name = type_["name"]
        if type_["kind"] == "UNION":
            msg = f"cannot attribute union type {name!r}: unions are not supported"
            raise PartitionError(msg)
        if type_["kind"] not in _ATTRIBUTABLE_KINDS:
            continue
        if owner := owner_of(type_.get("directives")):
            types[name] = owner
            continue
        for field_ in type_.get("fields") or ():
            if owner := owner_of(field_.get("directives")):
                fields[(name, field_["name"])] = owner
    return Ownership(types, fields)


def narrow_to_core(schema: Introspection) -> Introspection:
    """A copy of the schema holding only what no module owns.

    Owned types go, owned fields on core types go, and references to a dropped
    type from an interface's ``possibleTypes`` go with them. A module-owned type
    is only ever referenced by module-owned symbols (the engine rejects a module
    that exposes another module's types), so the result is closed; building a
    client schema from it is what proves that.
    """
    owned = ownership(schema)
    narrowed = copy.deepcopy(schema)
    kept: list[Introspection] = []
    for type_ in narrowed["types"]:
        if type_["name"] in owned.types:
            continue
        if type_.get("fields") is not None:
            type_["fields"] = [
                f
                for f in type_["fields"]
                if (type_["name"], f["name"]) not in owned.fields
            ]
        if type_.get("possibleTypes") is not None:
            type_["possibleTypes"] = [
                t for t in type_["possibleTypes"] if t["name"] not in owned.types
            ]
        kept.append(type_)
    narrowed["types"] = kept
    return narrowed


def entry_field(schema: Introspection, module: ModuleName) -> Introspection:
    """The ``Query`` field the module contributes: its entry point.

    The module's root type is the return type of that field. It is read off the
    schema rather than derived from the module name, because the engine's name
    for it is not a capitalization rule (module ``e2e`` has root type ``E2E``).
    """
    query = next(t for t in schema["types"] if t["name"] == QUERY_TYPE)
    owned = [
        f
        for f in query.get("fields") or ()
        if owner_of(f.get("directives")) == module
        and _named_type(f["type"])["kind"] == "OBJECT"
    ]
    if len(owned) != 1:
        msg = (
            f"module {module!r} contributes {len(owned)} Query fields, "
            "expected exactly 1"
        )
        raise PartitionError(msg)
    return owned[0]


def root_type_for(schema: Introspection, module: ModuleName) -> TypeName:
    """Name of the module's root object type."""
    return _named_type(entry_field(schema, module)["type"])["name"]


def _named_type(type_ref: Introspection) -> Introspection:
    while type_ref.get("ofType"):
        type_ref = type_ref["ofType"]
    return type_ref


def client_module_names(modules: Sequence[ModuleName]) -> list[str]:
    """The client module names of several bound modules, rejecting a collision.

    Two clients of one consumer that normalize to the same file (``foo-bar``
    and ``foo_bar``) would overwrite each other, so they fail here.
    """
    names = [client_module_name(module) for module in modules]
    taken: dict[str, ModuleName] = {}
    for module, name in zip(modules, names, strict=True):
        if name in taken:
            msg = (
                f"modules {taken[name]!r} and {module!r} would share the same "
                f"client file: {name}.py"
            )
            raise PartitionError(msg)
        taken[name] = module
    return names


def client_module_name(module: ModuleName) -> str:
    """The Python module name of a bound module's client, under ``dagger.clients``."""
    from codegen.generator import format_name

    name = format_name(module.replace("-", "_").replace(".", "_"))
    if not name.isidentifier() or keyword.iskeyword(name) or name.startswith("__"):
        msg = (
            f"module name {module!r} does not normalize to a usable Python module name"
        )
        raise PartitionError(msg)
    return name
