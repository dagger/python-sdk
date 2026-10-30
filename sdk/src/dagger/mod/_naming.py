"""How the engine names a module's own declarations.

The runtime registers Python names (``get_url``, ``HttpFetcher``) and the
engine turns them into schema names (``getUrl``, ``NamingAppHTTPFetcher``) by
the naming rules of the version it names the module by. Two things in the
runtime depend on those rules:

- which of the module's classes is its main object, the one the engine finds
  by the module's name;
- the calls the runtime builds itself, through the module's own interfaces,
  which select schema names: a wrong one is a fragment on a type that doesn't
  exist, a field the interface doesn't have, or an unknown enum member.

The runtime doesn't predict either: it asks the engine (``ModuleNaming``),
which answers by the same rules it names the module by, legacy strcase for a
module declaring a version before the identifier casing cut. It asks once,
when the module is loaded, in one query: for the module it is running
(``CurrentModule.naming``), or, when generation renders a module's entrypoint
from outside of it, for the module source generation names
(``ModuleSource.naming``, by the ID in ``SOURCE_ID_ENV``).
"""

from __future__ import annotations

import dataclasses
import os
import typing
from collections.abc import Awaitable, Callable, Iterable, Sequence

from dagger.client._core import to_literal
from dagger.client._session import default_session
from dagger.mod._exceptions import ModuleError

if typing.TYPE_CHECKING:
    import enum

    from dagger.mod._resolver import ObjectType

SOURCE_ID_ENV = "DAGGER_MODULE_SOURCE_ID"
"""The ID of the ModuleSource to ask about, for a module not running in it."""

Execute: typing.TypeAlias = Callable[[str], Awaitable[typing.Any]]
"""Runs a GraphQL query against the engine and returns its data."""


async def _execute(query: str) -> typing.Any:
    return await default_session().execute(query)


@dataclasses.dataclass(frozen=True, slots=True)
class ModuleNames:
    """The engine's names for the module's declarations, by declared name."""

    types: typing.Mapping[str, str] = dataclasses.field(default_factory=dict)
    """Namespaced schema names of the module's interfaces."""

    fields: typing.Mapping[str, str] = dataclasses.field(default_factory=dict)
    """Schema names of the interfaces' functions and arguments."""

    enum_members: typing.Mapping[str, str] = dataclasses.field(default_factory=dict)
    """Schema names of the members of the module's enums."""

    def type_name(self, name: str) -> str:
        """The schema name of an interface the module declares."""
        return self._get(self.types, name, "type")

    def field_name(self, name: str) -> str:
        """The schema name of an interface's function or argument."""
        return self._get(self.fields, name, "function or argument")

    def enum_member_name(self, name: str) -> str:
        """The schema name of a member of one of the module's enums."""
        return self._get(self.enum_members, name, "enum member")

    @staticmethod
    def _get(names: typing.Mapping[str, str], name: str, what: str) -> str:
        try:
            return names[name]
        except KeyError:
            # Every name a call through an interface uses is asked about
            # before anything runs; this one wasn't.
            msg = f"The engine wasn't asked for the schema name of {what} {name!r}"
            raise ModuleError(msg) from None


@dataclasses.dataclass(frozen=True, slots=True)
class Naming:
    """The engine's answers for a module."""

    main_object: str | None = None
    """Which candidate the engine takes as the main object; None if it wasn't
    asked, or takes none."""

    names: ModuleNames = dataclasses.field(default_factory=ModuleNames)


async def ask_naming(
    *,
    candidates: Sequence[str] | None = None,
    interfaces: Iterable[ObjectType] = (),
    enums: Iterable[type[enum.Enum]] = (),
    source_id: str | None = None,
    execute: Execute = _execute,
) -> Naming:
    """Ask the engine, in one query, how it names the module.

    ``candidates`` are the classes that may be the main object, which is
    asked only when given. The names are those of the interfaces, their
    functions and arguments, and the members of the module's enums, which
    calls through an interface send and receive; asked only for a module
    with interfaces. ``source_id`` names the module source to ask about;
    without it, the module the runtime runs in.
    """
    interfaces = list(interfaces)
    asked: dict[str, list[str]] = {}
    if interfaces:
        asked["typeNames"] = sorted({iface.cls.__name__ for iface in interfaces})
        asked["fieldNames"] = sorted(
            {
                name
                for iface in interfaces
                for fn in iface.functions.values()
                for name in (fn.name, *(p.name for p in fn.parameters.values()))
            }
        )
        asked["enumMemberNames"] = sorted(
            {member.name for cls in enums for member in cls}
        )
    selections = [
        f"{field}(names: {to_literal(names)})"
        for field, names in asked.items()
        if names
    ]
    if candidates is not None:
        selections.append(f"mainObject(candidates: {to_literal(list(candidates))})")
    if not selections:
        return Naming()

    fields = "".join(f"      {s}\n" for s in selections)
    if source_id is None:
        root, open_, close = "currentModule", "currentModule {", "}"
    else:
        root = "node"
        open_ = f"node(id: {to_literal(source_id)}) {{ ... on ModuleSource {{"
        close = "} }"
    query = f"query ModuleNaming {{\n  {open_}\n    naming {{\n{fields}"
    data = await execute(f"{query}    }}\n  {close}\n}}")
    naming = ((data or {}).get(root) or {}).get("naming")
    if not isinstance(naming, dict):
        msg = f"The engine answered no ModuleNaming: {data!r}"
        raise ModuleError(msg)

    def answers(field: str) -> dict[str, str]:
        names = asked.get(field)
        if not names:
            return {}
        got = naming.get(field)
        if not isinstance(got, list) or len(got) != len(names):
            msg = f"ModuleNaming.{field} returned {got!r} for {names!r}"
            raise ModuleError(msg)
        return dict(zip(names, got, strict=True))

    return Naming(
        main_object=naming.get("mainObject") if candidates is not None else None,
        names=ModuleNames(
            types=answers("typeNames"),
            fields=answers("fieldNames"),
            enum_members=answers("enumMemberNames"),
        ),
    )


def source_id() -> str | None:
    """The module source generation asks about, if it gave one."""
    return os.environ.get(SOURCE_ID_ENV) or None
