import enum
import sys
import types

import pytest

from dagger.client.base import Scalar, Type
from dagger.mod._converter import to_typedef
from dagger.mod._exceptions import ModuleLoadError
from dagger.mod.cli import load_module


@pytest.fixture
def client_module(monkeypatch):
    """Types the way a generated client under dagger.clients defines them."""
    module = types.ModuleType("dagger.clients.hello")
    monkeypatch.setitem(sys.modules, "dagger.clients.hello", module)

    class Hello(Type):
        __slots__ = ()

        async def id(self) -> str:
            return ""

    class HelloKind(enum.Enum):
        FULL = "FULL"

    class HelloToken(Scalar):
        __slots__ = ()

    class _NotGenerated:
        """The bootstrap stub's placeholder."""

    for cls in (Hello, HelloKind, HelloToken, _NotGenerated):
        cls.__module__ = "dagger.clients.hello"
        setattr(module, cls.__name__, cls)
    return module


@pytest.mark.parametrize("name", ["Hello", "HelloKind", "HelloToken", "_NotGenerated"])
@pytest.mark.parametrize("context", ["return type", "argument"])
def test_client_types_are_not_module_api_types(client_module, name, context):
    to_typedef.cache_clear()
    with pytest.raises(TypeError, match="generated client type"):
        to_typedef(getattr(client_module, name), context)


@pytest.mark.parametrize("missing", ["dagger.clients.hello", "dagger.clients"])
def test_missing_client_is_a_generate_and_commit_error(monkeypatch, missing):
    class Broken:
        module = "hello"

        @staticmethod
        def load():
            msg = f"No module named {missing!r}"
            raise ModuleNotFoundError(msg, name=missing)

    monkeypatch.setattr("dagger.mod.cli.get_entry_point", Broken)
    with pytest.raises(ModuleLoadError, match="run `dagger generate` and commit"):
        load_module()
