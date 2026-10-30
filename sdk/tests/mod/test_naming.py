"""Calls through a module's own interfaces, named as the engine names them.

The gate is the module session's schema having ``Query.formatIdentifiers``.
"""

import enum
import typing
from typing import Self

import pytest

import dagger
from dagger._exceptions import QueryError, QueryErrorValue
from dagger.client._core import Context, EnumName
from dagger.mod import Module, _naming
from dagger.mod._converter import to_interface_impl
from dagger.mod._naming import (
    SchemaNames,
    format_names,
    names_schemas,
    resolve_schema_names,
    supports_identifiers,
)

pytestmark = [
    pytest.mark.anyio,
]

# How an engine from v1.0.0-beta.21 formats the names below: acronyms are
# words of their own, which a plain case conversion can't tell.
ENGINE_NAMES = {
    "CAMEL": {
        "get_url": "getURL",
        "com_url": "comURL",
        "with_http_client": "withHTTPClient",
        "speak": "speak",
        "name": "name",
        "fetch": "fetch",
        "from": "from",
        "wait": "wait",
        "status": "status",
        "statuses": "statuses",
    },
    "SNAKE": {
        "Pond": "pond",
        "HttpFetcher": "http_fetcher",
        "PondSpeaker": "pond_speaker",
        "Waiter": "waiter",
    },
    "PASCAL": {
        "HttpFetcher": "HTTPFetcher",
        "PondSpeaker": "PondSpeaker",
        "Pond_HttpFetcher": "PondHTTPFetcher",
        "Pond_PondSpeaker": "PondPondSpeaker",
        "Waiter": "Waiter",
        "Pond_Waiter": "PondWaiter",
    },
    "SCREAMING_SNAKE": {
        "Ready": "READY",
        "HttpOk": "HTTP_OK",
    },
}


async def engine_formatter(names: list[str], casing: str) -> list[str]:
    return [ENGINE_NAMES[casing][name] for name in names]


@pytest.fixture(autouse=True)
def fresh_bindings():
    to_interface_impl.cache_clear()
    yield
    to_interface_impl.cache_clear()


@pytest.fixture
def mod() -> Module:
    m = Module("Pond")

    @m.interface
    class HttpFetcher(typing.Protocol):
        @m.function
        def get_url(self, com_url: str) -> Self: ...

        @m.function
        def with_http_client(self, name: str) -> Self: ...

        @m.function
        def fetch(self, from_: str) -> Self: ...

    # Already starts with the module's name: not namespaced again.
    @m.interface
    class PondSpeaker(typing.Protocol):
        @m.function
        def speak(self) -> Self: ...

    @m.object_type
    class Pond:
        fetcher: HttpFetcher = dagger.field()

    return m


def interface(mod: Module, name: str) -> type:
    return mod.get_object(name).cls


def interfaces(mod: Module):
    return [obj for obj in mod._objects.values() if obj.interface]


def selection(obj) -> tuple[str, str, dict]:
    field = obj._ctx.selections[-1]
    return field.type_name, field.name, field.args


async def test_resolve_schema_names(mod: Module):
    names = await resolve_schema_names("Pond", interfaces(mod), engine_formatter)

    assert names is not None
    assert names.fields == {
        "com_url": "comURL",
        "fetch": "fetch",
        "from": "from",
        "get_url": "getURL",
        "name": "name",
        "speak": "speak",
        "with_http_client": "withHTTPClient",
    }
    assert names.types == {
        interface(mod, "HttpFetcher"): "PondHTTPFetcher",
        interface(mod, "PondSpeaker"): "PondSpeaker",
    }


async def test_resolve_schema_names_without_interfaces():
    async def fail(*_):
        pytest.fail("unexpected call to the engine")

    assert await resolve_schema_names("Pond", [], fail) is None


async def test_resolve_schema_names_before_the_cut(
    mod: Module, monkeypatch: pytest.MonkeyPatch
):
    async def unsupported():
        return False

    monkeypatch.setattr(_naming, "supports_identifiers", unsupported)

    assert await resolve_schema_names("Pond", interfaces(mod)) is None


def test_legacy_schema_names():
    # Modules before v1.0.0-beta.21 keep the conversion they always had.
    names = SchemaNames()

    class HttpFetcher: ...

    assert names.field_name("get_url") == "getUrl"
    assert names.field_name("com_url") == "comUrl"
    assert names.interface_name("Pond", HttpFetcher) == "PondHttpFetcher"


@pytest.mark.parametrize(
    ("schema_names", "expected"),
    [
        # before the cut
        (False, ("PondHttpFetcher", "getUrl", {"comUrl": "x"})),
        # from v1.0.0-beta.21
        (True, ("PondHTTPFetcher", "getURL", {"comURL": "x"})),
    ],
)
async def test_interface_call_names(mod: Module, schema_names: bool, expected):
    if schema_names:
        names = await resolve_schema_names("Pond", interfaces(mod), engine_formatter)
        assert names is not None
        mod._schema_names = names

    impl = to_interface_impl(interface(mod, "HttpFetcher"))
    assert impl.__name__ == expected[0]
    assert impl._graphql_name() == expected[0]

    obj = impl(Context())
    assert selection(obj.get_url(com_url="x")) == expected


async def test_interface_call_renamed_arg(mod: Module):
    # `from_` is registered as `from`: bind by the Python name, send the other.
    impl = to_interface_impl(interface(mod, "HttpFetcher"))

    obj = impl(Context())
    assert selection(obj.fetch(from_="x")) == (
        "PondHttpFetcher",
        "fetch",
        {"from": "x"},
    )


async def test_load_schema_names(mod: Module, monkeypatch: pytest.MonkeyPatch):
    async def resolve(main_name, ifaces, **kwargs):
        return await resolve_schema_names(main_name, ifaces, engine_formatter, **kwargs)

    monkeypatch.setattr("dagger.mod._module.resolve_schema_names", resolve)
    stale = to_interface_impl(interface(mod, "HttpFetcher"))

    await mod.load_schema_names()

    impl = to_interface_impl(interface(mod, "HttpFetcher"))
    assert impl is not stale
    assert impl.__name__ == "PondHTTPFetcher"


async def test_load_schema_names_without_interfaces(monkeypatch: pytest.MonkeyPatch):
    async def fail(*_, **__):
        pytest.fail("unexpected call to the engine")

    monkeypatch.setattr("dagger.mod._module.resolve_schema_names", fail)
    m = Module("Pond")

    @m.object_type
    class Pond:
        name: str = dagger.field()

    await m.load_schema_names()


async def test_interface_call_enum_members():
    # The engine names a module's enum members SCREAMING_SNAKE (Ready is
    # READY), so a call through an interface sends that, not the Python name.
    m = Module("Pond")

    @m.enum_type
    class Status(enum.Enum):
        Ready = "ready"
        HttpOk = "ok"
        DONE = "done"

    @m.interface
    class Waiter(typing.Protocol):
        @m.function
        def wait(self, status: Status, statuses: list[Status]) -> Self: ...

    @m.object_type
    class Pond:
        waiter: Waiter = dagger.field()

    names = await resolve_schema_names(
        "Pond", interfaces(m), engine_formatter, enums=[Status]
    )
    assert names is not None
    assert names.enum_members == {
        Status: {"Ready": "READY", "HttpOk": "HTTP_OK", "DONE": "DONE"},
    }

    # Before the cut, the client sends a member by its Python name.
    obj = to_interface_impl(interface(m, "Waiter"))(Context())
    _, _, args = selection(obj.wait(status=Status.Ready, statuses=[Status.HttpOk]))
    assert args == {"status": Status.Ready, "statuses": [Status.HttpOk]}

    m._schema_names = names
    to_interface_impl.cache_clear()
    obj = to_interface_impl(interface(m, "Waiter"))(Context())
    _, _, args = selection(
        obj.wait(status=Status.Ready, statuses=[Status.HttpOk, Status.DONE])
    )
    assert args == {"status": "READY", "statuses": ["HTTP_OK", "DONE"]}
    assert isinstance(args["status"], EnumName)
    # Bare in the query, as enum values.
    query = obj.wait(status=Status.Ready, statuses=[Status.HttpOk])._ctx.build()
    assert "wait(status: READY, statuses: [HTTP_OK])" in query


@pytest.mark.parametrize("schema_names", [False, True])
async def test_interface_call_enum_results(
    monkeypatch: pytest.MonkeyPatch, schema_names: bool
):
    # The engine returns a module's enum members by the names it gave them.
    m = Module("Pond")

    @m.enum_type
    class Status(enum.Enum):
        Ready = "ready"
        HttpOk = "ok"

    @m.interface
    class Waiter(typing.Protocol):
        @m.function
        async def status(self) -> Status: ...

        @m.function
        async def statuses(self) -> list[Status]: ...

    @m.object_type
    class Pond:
        waiter: Waiter = dagger.field()

    if schema_names:
        names = await resolve_schema_names(
            "Pond", interfaces(m), engine_formatter, enums=[Status]
        )
        assert names is not None
        m._schema_names = names
        status, statuses = "READY", ["HTTP_OK", "READY"]
    else:
        # Before the cut, they're the Python names.
        status, statuses = "Ready", ["HttpOk", "Ready"]

    async def execute(ctx, return_type=None):
        value = statuses if ctx.selections[-1].name == "statuses" else status
        return ctx.converter.structure(value, return_type)

    monkeypatch.setattr(Context, "execute", execute)
    obj = to_interface_impl(interface(m, "Waiter"))(Context())

    assert await obj.status() is Status.Ready
    assert await obj.statuses() == [Status.HttpOk, Status.Ready]


def validation_error() -> QueryError:
    return QueryError(
        [
            QueryErrorValue(
                'Cannot query field "formatIdentifiers" on type "Query".',
                extensions={"code": "GRAPHQL_VALIDATION_FAILED"},
            )
        ],
        "query",
    )


@pytest.fixture
def unknown_support(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_naming, "_supported", None)

    async def engine_version():
        return "v1.0.0-beta.21"

    monkeypatch.setattr(_naming, "_engine_version", engine_version)


@pytest.mark.usefixtures("unknown_support")
async def test_supports_identifiers_released_before_the_cut(
    monkeypatch: pytest.MonkeyPatch,
):
    # Released engines before v1.0.0-beta.21 serve the API in every v1 view,
    # but name modules by their legacy rules.
    async def engine_version():
        return "v1.0.0-beta.20+0da36c8d"

    async def format_identifiers(*_):
        pytest.fail("unexpected call to formatIdentifiers")

    monkeypatch.setattr(_naming, "_engine_version", engine_version)
    monkeypatch.setattr(_naming, "_format_identifiers", format_identifiers)

    assert not await supports_identifiers()


@pytest.mark.parametrize(
    ("engine", "expected"),
    [
        ("v1.0.0-beta.19", False),
        ("v1.0.0-beta.20+0da36c8d", False),
        ("v0.21.9", False),
        ("v1.0.0-beta.21", True),
        ("v1.0.0-beta.100", True),
        ("v1.0.0-dev-0123456789ab", True),
        ("v1.0.0-rc.1", True),
        ("v1.0.0", True),
        ("v1.1.0", True),
        ("devel", True),
    ],
)
def test_names_schemas(engine: str, expected: bool):
    assert names_schemas(engine) is expected


@pytest.mark.usefixtures("unknown_support")
async def test_supports_identifiers(monkeypatch: pytest.MonkeyPatch):
    calls = []

    async def format_identifiers(names, casing):
        calls.append((names, casing))
        return []

    monkeypatch.setattr(_naming, "_format_identifiers", format_identifiers)

    assert await supports_identifiers()
    assert await supports_identifiers()
    assert calls == [([], "CAMEL")]  # asked once


@pytest.mark.usefixtures("unknown_support")
async def test_supports_identifiers_before_the_cut(monkeypatch: pytest.MonkeyPatch):
    # A view without the API refuses the query: the module keeps the old
    # conversion.
    async def format_identifiers(*_):
        raise validation_error()

    monkeypatch.setattr(_naming, "_format_identifiers", format_identifiers)

    assert not await supports_identifiers()


@pytest.mark.usefixtures("unknown_support")
async def test_supports_identifiers_other_errors(monkeypatch: pytest.MonkeyPatch):
    async def format_identifiers(*_):
        raise QueryError([QueryErrorValue("boom")], "query")

    monkeypatch.setattr(_naming, "_format_identifiers", format_identifiers)

    with pytest.raises(QueryError, match="boom"):
        await supports_identifiers()


def test_format_identifiers_query():
    ctx = Context().root_select(
        "formatIdentifiers",
        [
            _naming.Arg("names", ["get_url"]),
            _naming.Arg("casing", EnumName("CAMEL")),
        ],
    )
    assert ctx.build() == (
        'query {\n  formatIdentifiers(names: ["get_url"], casing: CAMEL)\n}'
    )


async def test_format_names(monkeypatch: pytest.MonkeyPatch):
    async def format_identifiers(names, casing):
        return [ENGINE_NAMES[casing][n] for n in names]

    monkeypatch.setattr(_naming, "_format_identifiers", format_identifiers)

    assert await format_names(["get_url", "com_url"], "CAMEL") == [
        "getURL",
        "comURL",
    ]


async def test_format_names_unparseable(monkeypatch: pytest.MonkeyPatch):
    # The engine keeps a name it can't parse as it is; one of those fails a
    # whole batch, so the rest are formatted one by one.
    async def format_identifiers(*_):
        raise QueryError([QueryErrorValue("can't parse name")], "query")

    async def format_name(name, casing):
        if not name.isascii():
            return name
        return ENGINE_NAMES[casing][name]

    monkeypatch.setattr(_naming, "_format_identifiers", format_identifiers)
    monkeypatch.setattr(_naming, "_format_name", format_name)

    assert await format_names(["get_url", "café"], "CAMEL") == ["getURL", "café"]
