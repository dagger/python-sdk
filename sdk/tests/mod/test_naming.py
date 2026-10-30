"""How the runtime gets the schema names of the module's declarations."""

import enum
import typing

import pytest

import dagger
from dagger.mod import Module
from dagger.mod._exceptions import ModuleError
from dagger.mod._naming import ModuleNames, module_names

pytestmark = [
    pytest.mark.anyio,
]

# CurrentModule.naming's answers for a module "naming-app" at
# v1.0.0-beta.22, as the engine's identifier naming rules name it.
NAMES = {
    "typeNames": {"HttpFetcher": "NamingAppHTTPFetcher"},
    "fieldNames": {
        "with_arch": "withArch",
        "arch": "arch",
        "also": "also",
        "e2e_test": "e2eTest",
        "arches": "arches",
    },
    "enumMemberNames": {"arm64": "ARM64", "AMD64": "AMD64"},
}


@pytest.fixture
def mod() -> Module:
    m = Module("NamingApp")

    @m.enum_type
    class Arch(enum.Enum):
        arm64 = "arm64"
        AMD64 = "amd64"

    @m.interface
    class HttpFetcher(typing.Protocol):
        @m.function
        def with_arch(self, arch: Arch, also: list[Arch]) -> typing.Self: ...

        @m.function
        async def e2e_test(self) -> str: ...

        @m.function
        async def arch(self) -> Arch: ...

        @m.function
        async def arches(self) -> list[Arch]: ...

    @m.object_type
    class NamingApp:
        fetcher: HttpFetcher = dagger.field()

    return m


async def fetcher(mod: Module):
    r, _ = await mod.get_structured_result(
        "NamingApp",
        {"fetcher": "fetcher-id"},
        "fetcher",
        {},
    )
    return r


async def test_one_query(mod: Module, engine):
    engine.names = NAMES
    await mod.load_names()
    await mod.load_names()
    assert len(engine.naming_queries) == 1
    query = engine.naming_queries[0]
    assert 'typeNames(names: ["HttpFetcher"])' in query
    assert (
        'fieldNames(names: ["also", "arch", "arches", "e2e_test", "with_arch"])'
        in query
    )
    assert 'enumMemberNames(names: ["AMD64", "arm64"])' in query


async def test_no_interfaces_asks_nothing(engine):
    m = Module("Plain")

    @m.object_type
    class Plain:
        pass

    await m.load_names()
    assert engine.queries == []


async def test_calls_by_the_engines_names(mod: Module, engine):
    engine.names = NAMES
    await mod.load_names()
    r = await fetcher(mod)
    arch = mod._enums["Arch"]

    assert type(r).__name__ == "NamingAppHTTPFetcher"
    query = r.with_arch(arch.arm64, [arch.AMD64, arch.arm64])._ctx.build()
    assert "... on NamingAppHTTPFetcher" in query
    assert "withArch(arch: ARM64, also: [AMD64, ARM64])" in query

    engine.data = {"node": {"e2eTest": "ok"}}
    assert await r.e2e_test() == "ok"
    assert "e2eTest" in engine.queries[-1]


async def test_enum_results(mod: Module, engine):
    engine.names = NAMES
    await mod.load_names()
    r = await fetcher(mod)
    arch = mod._enums["Arch"]

    engine.data = {"node": {"arch": "ARM64"}}
    assert await r.arch() is arch.arm64

    engine.data = {"node": {"arches": ["AMD64", "ARM64"]}}
    assert await r.arches() == [arch.AMD64, arch.arm64]

    engine.data = {"node": {"arch": "ARM_64"}}
    with pytest.raises(TypeError, match="Unexpected member 'ARM_64'"):
        await r.arch()


async def test_names_are_asked_before_a_call(mod: Module):
    with pytest.raises(ModuleError, match="not asked for"):
        await fetcher(mod)


def test_unasked_name_fails():
    with pytest.raises(ModuleError, match="'get_url'"):
        ModuleNames().field_name("get_url")


async def test_engine_answer_mismatch(mod: Module):
    async def execute(_):
        return {"currentModule": {"naming": {"typeNames": []}}}

    interfaces = [o for o in mod._objects.values() if o.interface]
    with pytest.raises(ModuleError, match="typeNames"):
        await module_names(interfaces, (), execute)
