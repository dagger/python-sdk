"""Python names for the schema's identifiers, as the engine spells them.

Every Python identifier the generator styles from the schema (methods,
arguments, input fields) is the schema name in snake_case, and the engine is
the one that spells it: ``Query.formatIdentifiers(casing: SNAKE)``, at the
schema's version, so the words are cut by the engine's naming dictionary
(``withGPU`` is ``with_gpu``, ``experimentalWithAllGPUs`` is
``experimental_with_all_gpus``) rather than by a converter of the SDK's own.

The generator asks once, up front, for every name in the schema, and asks
directly for a name it meets that wasn't among them. It runs as a nested
client of the Dagger session (``experimentalPrivilegedNesting``), which it
reaches through ``DAGGER_SESSION_PORT`` and ``DAGGER_SESSION_TOKEN``. A name
the engine can't format fails the generation.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import typing
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator

from graphql import (
    GraphQLInputObjectType,
    GraphQLInterfaceType,
    GraphQLObjectType,
    GraphQLSchema,
)

Format: typing.TypeAlias = Callable[[list[str]], list[str]]
"""Formats names in snake_case, in order."""


class NamingError(Exception):
    """The engine couldn't name an identifier."""


class Session:
    """The Dagger session the generator runs in, as a nested client."""

    def __init__(self, port: str, token: str):
        self.url = f"http://127.0.0.1:{port}/query"
        self.auth = "Basic " + base64.b64encode(f"{token}:".encode()).decode()

    @classmethod
    def from_env(cls) -> Session:
        port = os.environ.get("DAGGER_SESSION_PORT")
        token = os.environ.get("DAGGER_SESSION_TOKEN")
        if not port or not token:
            msg = (
                "no Dagger session to name identifiers with: DAGGER_SESSION_PORT "
                "and DAGGER_SESSION_TOKEN are not set; run the generator as a "
                "nested client of a Dagger session"
            )
            raise NamingError(msg)
        return cls(port, token)

    def query(self, query: str, variables: dict[str, typing.Any]) -> typing.Any:
        body = json.dumps({"query": query, "variables": variables}).encode()
        request = urllib.request.Request(  # noqa: S310 - a localhost URL
            self.url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": self.auth},
        )
        try:
            with urllib.request.urlopen(request) as response:  # noqa: S310
                result = json.load(response)
        except urllib.error.HTTPError as e:
            result = json.load(e)
        if errors := result.get("errors"):
            msg = "; ".join(str(e.get("message", e)) for e in errors)
            raise NamingError(msg)
        return result["data"]

    def formatter(self, version: str) -> Format:
        """Format names with formatIdentifiers at a version (empty: the caller's)."""
        if version:
            query = (
                "query FormatIdentifiers($names: [String!]!, $version: String!) {\n"
                "  formatIdentifiers(names: $names, casing: SNAKE, version: $version)\n"
                "}"
            )
        else:
            query = (
                "query FormatIdentifiers($names: [String!]!) {\n"
                "  formatIdentifiers(names: $names, casing: SNAKE)\n"
                "}"
            )

        def fmt(names: list[str]) -> list[str]:
            variables: dict[str, typing.Any] = {"names": names}
            if version:
                variables["version"] = version
            formatted = self.query(query, variables)["formatIdentifiers"]
            if len(formatted) != len(names):
                msg = f"formatIdentifiers gave {len(formatted)} of {len(names)} names"
                raise NamingError(msg)
            return formatted

        return fmt


class SnakeNames:
    """Names in snake_case, formatted up front and on a miss."""

    def __init__(self, fmt: Format):
        self._fmt = fmt
        self._names: dict[str, str] = {}

    def prefetch(self, names: Iterable[str]) -> None:
        """Format every name not formatted yet, in one call."""
        missing = sorted({n for n in names if n not in self._names})
        if missing:
            self._names.update(zip(missing, self._fmt(missing), strict=True))

    def __call__(self, name: str) -> str:
        if name not in self._names:
            self.prefetch([name])
        return self._names[name]


def schema_names(schema: GraphQLSchema) -> Iterator[str]:
    """Every name in a schema the generator may style: types, fields, args."""
    for type_name, t in schema.type_map.items():
        if type_name.startswith("__"):
            continue
        yield type_name
        if isinstance(t, GraphQLObjectType | GraphQLInterfaceType):
            for field_name, f in t.fields.items():
                yield field_name
                yield from f.args
        elif isinstance(t, GraphQLInputObjectType):
            yield from t.fields


_current: SnakeNames | None = None


@contextlib.contextmanager
def styled_by(names: SnakeNames) -> Iterator[SnakeNames]:
    """Style identifiers by these names while generating."""
    global _current  # noqa: PLW0603
    previous, _current = _current, names
    try:
        yield names
    finally:
        _current = previous


def snake(name: str) -> str:
    """A schema name in snake_case, as the engine spells it."""
    if _current is None:
        msg = "no engine to name identifiers with (see codegen.naming.styled_by)"
        raise NamingError(msg)
    return _current(name)


def engine_names(schemas: Iterable[GraphQLSchema], version: str) -> SnakeNames:
    """The names of the schemas, formatted by the session's engine at version."""
    names = SnakeNames(Session.from_env().formatter(version))
    names.prefetch(n for schema in schemas for n in schema_names(schema))
    return names
