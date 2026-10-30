import typing
from typing import Self

import pytest

import dagger
from dagger.client.base import Interface
from dagger.mod import Module
from dagger.mod._utils import (
    is_dagger_interface_type,
    is_dagger_object_type,
)

pytestmark = [
    pytest.mark.anyio,
]

# What the engine names the module's declarations.
POND_NAMES = {
    "typeNames": {"Duck": "PondDuck", "Goose": "PondGoose"},
    "fieldNames": {
        "speak": "speak",
        "quack": "quack",
        "get_self": "getSelf",
        "get_mob": "getMob",
        "with_origin": "withOrigin",
        "from": "from",
        "destination": "destination",
    },
}


@pytest.fixture
def mod() -> Module:
    m = Module("Pond")

    @m.interface
    class Goose(typing.Protocol):
        @m.function
        def speak(self) -> str: ...

    @m.interface
    class Duck(typing.Protocol):
        @m.function
        def quack(self) -> str: ...

        @m.function
        def get_self(self) -> Self: ...

        @m.function
        def get_mob(self) -> list[Self]: ...

        @m.function
        def with_origin(
            self,
            from_: str,
            to: typing.Annotated[str, dagger.Name("destination")],
        ) -> Self: ...

        def get_private(self) -> bool: ...

    @m.object_type
    class Pond:
        duck: Duck = dagger.field()

    return m


@pytest.fixture
async def duck(mod: Module, engine):
    engine.names = POND_NAMES
    await mod.load_naming()
    r, t = await mod.get_structured_result(
        "Pond",
        {"duck": "123456"},
        "duck",
        {},
    )
    return r, t


def test_registered_functions(mod: Module):
    assert "quack" in mod.get_object("Duck").functions


async def test_generated_implementation(duck):
    r, t = duck

    assert t.return_type.__name__ == "Duck"
    assert is_dagger_interface_type(t.return_type)

    assert type(r).__name__ == "PondDuck"
    assert is_dagger_object_type(type(r))

    assert isinstance(r, Interface)
    assert hasattr(r, "quack")
    assert not hasattr(r, "get_private")


async def test_interface_object_chain(duck):
    r, _ = duck
    assert type(r.get_self()) is type(r)


async def test_interface_call_arguments(duck):
    """Arguments are bound by Python name and sent by their schema name."""
    r, _ = duck
    query = r.with_origin(from_="a", to="b")._ctx.build()
    assert 'withOrigin(from: "a", destination: "b")' in query
