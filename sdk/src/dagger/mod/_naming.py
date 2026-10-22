"""Schema names the engine gives a module's own declarations.

The runtime registers Python names (``get_url``) and the engine turns them
into schema names (``getURL``). Calls the runtime builds itself, against its
own module's interfaces, have to use those schema names.

From engine version v1.0.0-beta.21 the engine names a module's declarations
by its naming rules, which know acronyms (``URL``, ``HTTP``) a plain case
conversion can't. Rather than duplicate those rules here, the runtime asks the
engine to format its names (``Query.formatIdentifiers``). That API is only in
the views the engine names modules by those rules, and the runtime's session
is served in its module's view, so the API being there is the gate: older
modules keep the conversion they always had.
"""

import dataclasses
import enum
import logging
import re
import typing
from collections.abc import Awaitable, Callable, Iterable

from dagger._exceptions import QueryError
from dagger.client._core import Arg, Context, EnumName
from dagger.client._descriptor import missing_field
from dagger.mod._utils import to_camel_case

if typing.TYPE_CHECKING:
    from dagger.mod._resolver import ObjectType

logger = logging.getLogger(__package__)

CAMEL = "CAMEL"
PASCAL = "PASCAL"
SNAKE = "SNAKE"
SCREAMING_SNAKE = "SCREAMING_SNAKE"

Formatter: typing.TypeAlias = Callable[[list[str], str], Awaitable[list[str]]]
"""Format names in a casing (``CAMEL``, ``PASCAL``, ``SNAKE`` or
``SCREAMING_SNAKE``)."""

_CONVENTIONAL_ENUM_MEMBER = re.compile(r"[A-Z][A-Z0-9_]*")


@dataclasses.dataclass(slots=True)
class SchemaNames:
    """The schema names of a module's interfaces and their members.

    Names not found here fall back to the conversion modules get before
    v1.0.0-beta.21, which is also what an empty instance gives.
    """

    fields: dict[str, str] = dataclasses.field(default_factory=dict)
    """Function and argument names, from the name the runtime registers."""

    types: dict[type, str] = dataclasses.field(default_factory=dict)
    """Interface type names, from the interface's class."""

    enum_members: dict[type, dict[str, str]] = dataclasses.field(default_factory=dict)
    """Member names of the module's own enums, by enum class, from the
    member's Python name."""

    def field_name(self, name: str) -> str:
        """The schema name of a function or argument."""
        return self.fields.get(name) or to_camel_case(name)

    def interface_name(self, main_name: str, proto: type) -> str:
        """The schema name of an interface declared in the module."""
        return self.types.get(proto) or main_name + proto.__name__

    def enum_value(self, value: typing.Any) -> typing.Any:
        """A value as it goes over the wire in a call through an interface.

        A member of one of the module's own enums is sent by its schema name,
        which the engine formats (``Ready`` is ``READY``); anything else, and
        any member not found here, is left for the client to convert, which
        sends a member by its Python name.
        """
        if isinstance(value, list | tuple):
            return [self.enum_value(v) for v in value]
        if isinstance(value, enum.Enum) and (
            name := self.enum_members.get(type(value), {}).get(value.name)
        ):
            return EnumName(name)
        return value

    def enum_reader(self, cls: typing.Any) -> Callable[[str], enum.Enum] | None:
        """Read a member of one of the module's own enums by its schema name.

        The engine returns a member by the name it gave it (``READY``), not
        by its Python name (``Ready``). None for any other type.
        """
        members = self.enum_members.get(cls) if isinstance(cls, type) else None
        if not members:
            return None
        python = {schema: member for member, schema in members.items()}
        return lambda name: cls[python.get(name, name)]


async def resolve_schema_names(
    main_name: str,
    interfaces: Iterable["ObjectType"],
    formatter: Formatter | None = None,
    enums: Iterable[type[enum.Enum]] = (),
) -> SchemaNames | None:
    """Ask the engine for the schema names of the module's interfaces.

    The member names of the module's own ``enums`` are resolved too, since
    calls through an interface can pass them.

    Returns None if the engine doesn't name the module by its naming rules
    (modules before v1.0.0-beta.21).
    """
    interfaces = list(interfaces)
    if not interfaces:
        return None

    if formatter is None:
        if not await supports_identifiers():
            return None
        formatter = format_names

    names = sorted(
        {
            name
            for iface in interfaces
            for fn in iface.functions.values()
            for name in (fn.name, *(p.name for p in fn.parameters.values()))
            if name
        }
    )
    protos = [iface.cls for iface in interfaces]
    proto_names = [proto.__name__ for proto in protos]

    camel = await formatter(names, CAMEL) if names else []
    snake = await formatter([main_name, *proto_names], SNAKE)
    pascal = await formatter(
        [*proto_names, *(f"{main_name}_{name}" for name in proto_names)],
        PASCAL,
    )

    # Like the engine, namespace an interface with the module's name unless
    # its name already starts with the module's name, comparing words.
    main_snake, proto_snakes = snake[0], snake[1:]
    types = {}
    for i, proto in enumerate(protos):
        proto_snake = proto_snakes[i]
        if proto_snake == main_snake or proto_snake.startswith(f"{main_snake}_"):
            types[proto] = pascal[i]
        else:
            types[proto] = pascal[len(protos) + i]

    return SchemaNames(
        fields=dict(zip(names, camel, strict=True)),
        types=types,
        enum_members=await _enum_member_names(enums, formatter),
    )


async def _enum_member_names(
    enums: Iterable[type[enum.Enum]],
    formatter: Formatter,
) -> dict[type, dict[str, str]]:
    """The schema names of the members of the module's enums, by enum class.

    Like the engine, members already named in GraphQL's convention (``READY``,
    ``HTTP2``) keep their name; the others are formatted SCREAMING_SNAKE.
    """
    enums = list(enums)
    todo = sorted(
        {
            member.name
            for cls in enums
            for member in cls
            if not _CONVENTIONAL_ENUM_MEMBER.fullmatch(member.name)
        }
    )
    formatted = (
        dict(zip(todo, await formatter(todo, SCREAMING_SNAKE), strict=True))
        if todo
        else {}
    )
    return {
        cls: {member.name: formatted.get(member.name, member.name) for member in cls}
        for cls in enums
    }


_supported: bool | None = None

IDENTIFIER_CASING_VERSION = "v1.0.0-beta.21"
"""The first engine release that names modules by its naming rules."""


async def supports_identifiers() -> bool:
    """Whether the engine names the module by its naming rules.

    From v1.0.0-beta.21 the engine serves ``Query.formatIdentifiers`` in the
    views it names schemas by those rules, and only in those. The runtime's
    session is served in its module's view, so the API being there tells how
    the engine names the module. Released engines before that serve the API
    in every v1 view but name modules by their legacy rules, so the engine's
    version is checked first. Asked once per process.
    """
    global _supported  # noqa: PLW0603
    if _supported is None:
        if not names_schemas(await _engine_version()):
            _supported = False
            return _supported
        try:
            await _format_identifiers([], CAMEL)
        except QueryError as e:
            if missing_field(e) is None:
                raise
            _supported = False
        else:
            _supported = True
    return _supported


_SEMVER = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


def names_schemas(engine: str) -> bool:
    """Whether an engine release names schemas by its naming rules.

    A release from v1.0.0-beta.21 on. An unreadable version leaves the
    decision to the schema.
    """
    version, cut = _SEMVER.match(engine), _SEMVER.match(IDENTIFIER_CASING_VERSION)
    assert cut is not None
    if version is None:
        return True
    return not _precedes(version, cut)


def _precedes(a: re.Match[str], b: re.Match[str]) -> bool:
    """Semver precedence of two versions matched by ``_SEMVER``."""
    core_a, core_b = tuple(map(int, a.groups()[:3])), tuple(map(int, b.groups()[:3]))
    if core_a != core_b:
        return core_a < core_b
    pre_a, pre_b = a.group(4), b.group(4)
    if not pre_a or not pre_b:
        # A release follows its prereleases.
        return bool(pre_a) and not pre_b
    ids_a, ids_b = pre_a.split("."), pre_b.split(".")
    for x, y in zip(ids_a, ids_b, strict=False):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return int(x) < int(y)
        if x.isdigit() or y.isdigit():
            # Numbers come before words.
            return x.isdigit()
        return x < y
    return len(ids_a) < len(ids_b)


async def _engine_version() -> str:
    return await Context().root_select("version", []).execute(str)


async def format_names(names: list[str], casing: str) -> list[str]:
    """Format names with the engine's naming rules.

    A name the engine can't parse (non-ASCII, say) fails the whole batch.
    The engine leaves such a name unchanged when it names a module, so do
    the same, formatting the rest one by one.
    """
    try:
        return await _format_identifiers(names, casing)
    except QueryError:
        logger.debug("Failed to format names in a batch", exc_info=True)

    return [await _format_name(name, casing) for name in names]


async def _format_identifiers(names: list[str], casing: str) -> list[str]:
    # The SDK files can't import the generated bindings: select by name.
    ctx = Context().root_select(
        "formatIdentifiers",
        [Arg("names", names), Arg("casing", EnumName(casing))],
    )
    return await ctx.execute(list[str])


async def _format_name(name: str, casing: str) -> str:
    ctx = (
        Context()
        .root_select("identifier", [Arg("name", name)])
        .select("Identifier", "format", [Arg("casing", EnumName(casing))])
    )
    try:
        return await ctx.execute(str)
    except QueryError:
        return name
