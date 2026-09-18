"""Attribution of a schema's types and fields to core or to a client."""

import hashlib
from collections.abc import Iterable, Iterator
from keyword import iskeyword

import graphql
from graphql import (
    GraphQLEnumType,
    GraphQLField,
    GraphQLInputObjectType,
    GraphQLInterfaceType,
    GraphQLNamedType,
    GraphQLObjectType,
    GraphQLSchema,
)

CORE = "core"
"""Package name of the core bindings, so no client can take it."""

_FieldOwner = GraphQLObjectType | GraphQLInterfaceType


class ClientError(ValueError):
    """A client that can't be generated from the schema."""


class ClientNameError(ClientError):
    """A client name that can't become a package in `dagger_clients`."""


def source_module(node: graphql.Node | None) -> str | None:
    """Module that `@sourceMap(module:)` attributes a type or a field to."""
    for directive in getattr(node, "directives", None) or ():
        if directive.name.value != "sourceMap":
            continue
        for arg in directive.arguments or ():
            if arg.name.value == "module":
                return graphql.value_from_ast_untyped(arg.value) or None
    return None


def own_fields(t: _FieldOwner) -> dict[str, GraphQLField]:
    """Fields of a type, without those a client contributes to a core type."""
    if source_module(t.ast_node) is not None:
        return t.fields
    return {n: f for n, f in t.fields.items() if source_module(f.ast_node) is None}


def contributed_fields(
    schema: GraphQLSchema, module: str
) -> list[tuple[_FieldOwner, str, GraphQLField]]:
    """Fields that a module contributes to core types."""
    return [
        (t, name, f)
        for _, t in sorted(schema.type_map.items())
        if isinstance(t, _FieldOwner) and source_module(t.ast_node) is None
        for name, f in sorted(t.fields.items())
        if source_module(f.ast_node) == module
    ]


def check_attribution(schema: GraphQLSchema) -> None:
    """Refuse a member attributed to a module that can't get it.

    A client contributes fields to core object and interface types, and
    nothing else. Anything else with `@sourceMap` on it would silently land
    in the package of its type, so it's an error at generation.
    """
    for type_name, t in sorted(schema.type_map.items()):
        if type_name.startswith("__"):
            continue
        owner = source_module(t.ast_node)
        if isinstance(t, _FieldOwner | GraphQLInputObjectType):
            members = {n: f.ast_node for n, f in t.fields.items()}
        elif isinstance(t, GraphQLEnumType):
            members = {n: v.ast_node for n, v in t.values.items()}
        else:
            continue
        for name, node in sorted(members.items()):
            module = source_module(node)
            if module is None or module == owner:
                continue
            if owner is None and isinstance(t, _FieldOwner):
                continue
            place = f'"{type_name}.{name}" is attributed to the client "{module}"'
            if owner is not None:
                msg = (
                    f'{place}, but its type "{type_name}" '
                    f'is attributed to the client "{owner}"'
                )
            else:
                msg = (
                    f"{place}, but only a field of a core object or interface "
                    "type can be contributed"
                )
            raise ClientError(msg)


def modules(schema: GraphQLSchema) -> list[str]:
    """Every module the schema attributes something to."""

    def _attributions() -> Iterator[str | None]:
        for t in schema.type_map.values():
            yield source_module(t.ast_node)
            if isinstance(t, _FieldOwner):
                yield from (source_module(f.ast_node) for f in t.fields.values())

    return sorted({m for m in _attributions() if m is not None})


def package_name(name: str) -> str:
    """Package that a client name becomes in `dagger_clients`."""
    result = name.lower().replace("-", "_").replace(".", "_")
    if not result.isidentifier():
        msg = f'client name "{name}" is not a Python identifier: "{result}"'
        raise ClientNameError(msg)
    if iskeyword(result):
        msg = f'client name "{name}" is a Python keyword'
        raise ClientNameError(msg)
    if result.startswith("_"):
        msg = f'client name "{name}" starts with "_"'
        raise ClientNameError(msg)
    if result == CORE:
        msg = f'client name "{name}" is taken by the core bindings'
        raise ClientNameError(msg)
    return result


def package_names(names: Iterable[str]) -> dict[str, str]:
    """Package of each client name, refusing two clients with one package."""
    taken: dict[str, str] = {}
    for name in names:
        package = package_name(name)
        if taken.setdefault(package, name) != name:
            msg = (
                f'clients "{taken[package]}" and "{name}" '
                f'both become the package "{package}"'
            )
            raise ClientNameError(msg)
    return {name: package for package, name in taken.items()}


def core_digest(schema: GraphQLSchema, *, legacy_sdk_compat: bool = False) -> str:
    """Digest of what the schema holds for core, whatever its clients are."""
    # The version itself is not hashed: two versions that generate one core
    # must give one digest, and only the compatibility mode changes the code.
    digest = hashlib.sha256(b"legacy" if legacy_sdk_compat else b"")
    for name, t in sorted(schema.type_map.items()):
        if name.startswith("__") or source_module(t.ast_node) is not None:
            continue
        # A schema lists a built-in scalar only when something uses it, and
        # that may be a client.
        if graphql.is_specified_scalar_type(t):
            continue
        for line in _describe(t):
            digest.update(line.encode())
            digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def _describe(t: GraphQLNamedType) -> Iterator[str]:
    # Members go by name: the order the engine lists them in is not part of
    # the API, and must not make a client look stale.
    #
    # The printer drops applied directives, and the generated code depends on
    # them, for example on `@expectedType`. So they are listed apart.
    yield from _directives(t.name, t.ast_node)

    if isinstance(t, _FieldOwner):
        fields = dict(sorted(own_fields(t).items()))
        copy = type(t)(t.name, fields, t.interfaces, description=t.description)
        yield graphql.print_type(copy)
        for field_name, field in fields.items():
            path = f"{t.name}.{field_name}"
            yield from _directives(path, field.ast_node)
            for arg_name, arg in field.args.items():
                yield from _directives(f"{path}.{arg_name}", arg.ast_node)

    elif isinstance(t, GraphQLInputObjectType):
        inputs = dict(sorted(t.fields.items()))
        yield graphql.print_type(
            GraphQLInputObjectType(t.name, inputs, description=t.description)
        )
        for input_name, input_field in inputs.items():
            yield from _directives(f"{t.name}.{input_name}", input_field.ast_node)

    elif isinstance(t, GraphQLEnumType):
        values = dict(sorted(t.values.items()))
        yield graphql.print_type(
            GraphQLEnumType(t.name, values, description=t.description)
        )
        for value_name, value in values.items():
            yield from _directives(f"{t.name}.{value_name}", value.ast_node)

    else:
        yield graphql.print_type(t)


def _directives(path: str, node: graphql.Node | None) -> Iterator[str]:
    for directive in getattr(node, "directives", None) or ():
        yield f"{path} {graphql.print_ast(directive)}"
