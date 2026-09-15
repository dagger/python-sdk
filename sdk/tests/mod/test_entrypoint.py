import enum
import hashlib
import os
import pathlib
import subprocess
import sys
import typing
from typing import Annotated

import pytest
from typing_extensions import Doc, Self

import dagger
from dagger import DefaultPath, Ignore, Name
from dagger.mod import Module
from dagger.mod._entrypoint import (
    _quote,
    file_digest,
    render_main,
    render_types,
    source_files,
    write_entrypoint,
)
from dagger.mod._exceptions import BadUsageError

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

        source: dagger.Directory
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
            self, src: Annotated[dagger.Directory, DefaultPath("."), Ignore([".venv"])]
        ) -> list[Helper]: ...

        @m.function
        def mob(self, who: str | None = None) -> list[Self]: ...

        @m.function
        def greeter(self, g: Greeter) -> Greeter: ...

        @m.function(deprecated="use container")
        def old(self, p: dagger.Platform) -> dagger.JSON: ...

    return m


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "dagger-module.toml").write_text('name = "main"\n')
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "main"\n')
    (tmp_path / "src" / "main").mkdir(parents=True)
    (tmp_path / "src" / "main" / "__init__.py").write_text("x = 1\n")
    (tmp_path / "src" / "main" / "extra.py").write_text("y = 2\n")
    for skipped in ("sdk/src/dagger", ".venv/lib", "src/main/__pycache__", ".hidden"):
        (tmp_path / skipped).mkdir(parents=True)
        (tmp_path / skipped / "ignored.py").write_text("z = 3\n")
    return tmp_path


def _assert_golden(name: str, rendered: str):
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(rendered)
    assert rendered == path.read_text()


def test_types_golden(mod: Module):
    _assert_golden("types.dang", render_types(mod.describe()))


def test_main_golden(root: pathlib.Path):
    rendered = render_main("main", ".dagger/modules/main", source_files(root))
    _assert_golden("main.dang", rendered)


def test_source_files(root: pathlib.Path):
    assert [f.path for f in source_files(root)] == [
        ".python-version",
        "pyproject.toml",
        "requirements.lock",
        "uv.lock",
        "src/main/__init__.py",
        "src/main/extra.py",
    ]


def test_absent_manifest_is_recorded_without_a_digest(root: pathlib.Path):
    rendered = render_main("main", ".", source_files(root))
    assert 'SourceFile(path: "uv.lock", digest: ""),' in rendered


def test_file_digest():
    data = b"x = 1\n"
    inner = hashlib.sha256(data).digest()
    assert file_digest(data) == "sha256:" + hashlib.sha256(inner).hexdigest()


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


def test_refuses_cache_policy():
    mod = Module("Foo")

    @mod.object_type
    class Foo:
        @mod.function(cache="never")
        def fresh(self) -> str: ...

    with pytest.raises(BadUsageError, match="cache='never'"):
        render_types(mod.describe())


@pytest.mark.parametrize("path", ["/abs", "../up", "a/../../b"])
def test_path_must_stay_inside(path: str):
    with pytest.raises(BadUsageError, match="relative"):
        render_main("main", path, [])


def test_write_entrypoint(mod: Module, root: pathlib.Path):
    out = root / "out"
    write_entrypoint(mod.describe(), name="main", path=".", root=root, output=out)
    assert (out / "types.dang").read_text().startswith("# Code generated")
    assert 'SourceFile(path: "src/main/extra.py"' in (out / "main.dang").read_text()


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
            "--path",
            ".dagger/modules/hello",
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
    assert "SourceFile" in (tmp_path / "out" / "main.dang").read_text()
