import enum
import os
import pathlib
import subprocess
import sys
import typing
from typing import Annotated

import pytest
from typing_extensions import Doc, Self

from dagger import DefaultPath, Ignore, Name
from dagger.client import gen
from dagger.mod import Module
from dagger.mod._entrypoint import (
    _quote,
    render_main,
    render_types,
    write_entrypoint,
)

GOLDEN = pathlib.Path(__file__).parent / "golden"


class Color(enum.Enum):
    """A color."""

    RED = "red"
    """The red one."""

    BLUE = "blue"


@pytest.fixture
def mod() -> Module:
    m = Module("Main")
    m.enum_type(Color)

    @m.interface
    class Greeter(typing.Protocol):
        @m.function
        def greet(self, name: str) -> str: ...

    @m.object_type
    class Helper:
        """A helper."""

        level: Color = m.field()

    @m.object_type
    class Main:
        """The main object."""

        source: gen.Directory
        greeting: str = m.field(default="hello")
        count: Annotated[int, Doc("How many")] = m.field(default=1, name="howMany")

        @m.function
        def container(self, base: Annotated[str, Name("from")] = "alpine") -> str:
            r"""A "container".

            Built from\a base image.
            """
            return base

        @m.function
        @m.check
        def lint(self) -> None: ...

        @m.function
        def helpers(
            self, src: Annotated[gen.Directory, DefaultPath("."), Ignore([".venv"])]
        ) -> list[Helper]: ...

        @m.function
        def mob(self, who: str | None = None) -> list[Self]: ...

        @m.function
        def greeter(self, g: Greeter) -> Greeter: ...

        @m.function(deprecated="use container")
        def old(self, p: gen.Platform) -> gen.JSON: ...

    return m


def _assert_golden(name: str, rendered: str):
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(rendered)
    assert rendered == path.read_text()


def test_types_golden(mod: Module):
    _assert_golden("types.dang", render_types(mod.describe()))


def test_main_golden():
    _assert_golden("main.dang", render_main("main"))


def test_quoting():
    mod = Module("Foo")

    @mod.object_type
    class Foo:
        r"""Say "hi"\now.

        On a new line.
        """

    rendered = render_types(mod.describe())
    assert r'description: "Say \"hi\"\\now.\n\nOn a new line."' in rendered
    assert _quote("a\tb") == r'"a\tb"'


def test_one_constructor_no_cache_policy(mod: Module):
    rendered = render_types(mod.describe())
    assert rendered.count("withConstructor(") == 1
    assert "withCachePolicy" not in rendered


@pytest.mark.parametrize(
    ("cache", "policy"),
    [
        ("never", "FunctionCachePolicy.Never"),
        ("session", "FunctionCachePolicy.PerSession"),
        ("30m", 'FunctionCachePolicy.Default, timeToLive: "30m"'),
    ],
)
def test_renders_cache_policy(cache: str, policy: str):
    mod = Module("Foo")

    @mod.object_type
    class Foo:
        @mod.function(cache=cache)
        def fresh(self) -> str: ...

    assert f".withCachePolicy({policy})" in render_types(mod.describe())


def test_write_entrypoint(mod: Module, tmp_path: pathlib.Path):
    out = tmp_path / "out"
    write_entrypoint(mod.describe(), name="main", output=out)
    assert (out / "types.dang").read_text().startswith("# Code generated")
    assert 'let moduleName: String! = "main"' in (out / "main.dang").read_text()


def test_command(tmp_path: pathlib.Path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "hello"\n')
    pkg = tmp_path / "src" / "hello"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "from dagger import function, object_type\n\n"
        "@object_type\nclass Hello:\n"
        "    @function\n    def hi(self) -> str:\n        return 'hi'\n"
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "dagger.mod",
            "entrypoint",
            "--name",
            "hello",
            "--output",
            "out",
        ],
        cwd=tmp_path,
        env={
            "PATH": "",
            "PYTHONPATH": str(pkg.parent),
            "DAGGER_DEFAULT_PYTHON_PACKAGE": "hello",
            "DAGGER_MAIN_OBJECT": "Hello",
        },
        check=True,
    )
    types = (tmp_path / "out" / "types.dang").read_text()
    assert 'typeDef.withObject("Hello")' in types
    assert 'function("hi", typeDef.withKind(TypeDefKind.STRING_KIND))' in types
    assert (
        'let moduleName: String! = "hello"'
        in (tmp_path / "out" / "main.dang").read_text()
    )
