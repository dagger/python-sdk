"""What the dagger package is, with and without the temporary global client."""

import importlib.util
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest
from graphql import build_schema

import dagger
from codegen.packages import (
    client_package,
    core_package,
    global_package,
    write_global,
    write_package,
)
from dagger.client import Session, default_session

SDL = """
    directive @sourceMap(module: String, filename: String)
        on OBJECT | FIELD_DEFINITION | ENUM | ENUM_VALUE | INPUT_OBJECT
    directive @expectedType(name: String!) on FIELD_DEFINITION | ARGUMENT_DEFINITION

    scalar Platform
    type Directory { id: ID! @expectedType(name: "Directory") }
    type Container { id: ID! @expectedType(name: "Container") }
    type Binding {
        name: String!
        asLinter: Linter! @sourceMap(module: "linter")
    }
    type Linter @sourceMap(module: "linter") {
        id: ID! @expectedType(name: "Linter")
        lint(src: ID! @expectedType(name: "Directory")): String!
    }
    type Query {
        directory: Directory!
        container(platform: Platform): Container!
        linter(source: ID! @expectedType(name: "Directory")): Linter!
            @sourceMap(module: "linter")
    }
"""


def test_this_environment_has_no_global_client():
    assert importlib.util.find_spec("dagger_global") is None


def test_dag_is_the_default_session():
    assert type(dagger.dag) is Session
    assert dagger.dag is default_session()


def test_core_name_points_to_its_new_home():
    with pytest.raises(AttributeError) as info:
        _ = dagger.Container

    assert str(info.value) == (
        "module 'dagger' has no attribute 'Container'. Core types moved to "
        "dagger_clients.core: from dagger_clients.core import Container"
    )


def test_legacy_client_type_points_to_session():
    with pytest.raises(AttributeError) as info:
        _ = dagger.Client

    assert str(info.value) == (
        "module 'dagger' has no attribute 'Client'. dagger.Connection now yields "
        "a dagger.Session, and the API is on core() from dagger_clients.core."
    )


def test_other_missing_package_names_get_the_plain_message():
    plain = r"^module 'dagger' has no attribute 'nope'$"
    with pytest.raises(AttributeError, match=plain):
        _ = dagger.nope

    assert not hasattr(dagger, "Container")


def test_session_field_points_to_core():
    with pytest.raises(AttributeError) as info:
        dagger.dag.container()

    assert str(info.value) == (
        "'Session' object has no attribute 'container'. The API is on the "
        "clients now: core().container() for a core field, or container() from "
        "the client's package for a client. To keep dag.container() while "
        "migrating, set global-client = true under [tool.dagger] and run "
        "dagger generate."
    )
    assert info.value.name == "container"


def test_session_field_points_to_a_client_too():
    # The session cannot tell a client from a core field without importing
    # core, so one message has to be true of both.
    with pytest.raises(AttributeError) as info:
        dagger.dag.linter()

    assert "or linter() from the client's package for a client." in str(info.value)


def test_private_session_name_gets_the_plain_message():
    plain = r"^'Session' object has no attribute '_x'$"
    with pytest.raises(AttributeError, match=plain):
        _ = dagger.dag._x

    assert not hasattr(dagger.dag, "container")


def _run(script: str, pythonpath: pathlib.Path | None = None) -> str:
    env = dict(os.environ)
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


NO_GENERATED_CODE = """
    import importlib.abc
    import sys

    class Absent(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            generated = ("dagger_clients", "dagger_gen", "dagger_global")
            if name.partition(".")[0] in generated:
                raise ModuleNotFoundError(name)
            if name == "dagger.client.gen":
                raise RuntimeError("the legacy bindings were imported")

    sys.meta_path.insert(0, Absent())

    import dagger

    assert type(dagger.dag).__name__ == "Session", type(dagger.dag)
    try:
        dagger.Container
    except AttributeError as e:
        assert "dagger_clients.core" in str(e), e
    else:
        raise AssertionError("dagger.Container exists")
    try:
        dagger.dag.container()
    except AttributeError as e:
        assert "core().container()" in str(e), e
    else:
        raise AssertionError("dag.container exists")
    print("ok")
"""


def test_package_imports_with_no_generated_code():
    assert _run(NO_GENERATED_CODE) == "ok\n"


@pytest.fixture
def generated(tmp_path: pathlib.Path) -> pathlib.Path:
    """Core, the linter client and the global client, as an installed scope."""
    schema = build_schema(SDL)
    write_package(tmp_path, "core", core_package(schema))
    write_package(tmp_path, *client_package(schema, "linter", "./linter"))
    write_global(tmp_path, global_package([schema]))
    return tmp_path


WITH_GLOBAL_CLIENT = """
    import dagger
    import dagger_global
    import dagger_clients.core as core
    import dagger_clients.linter as linter
    from dagger.client import Session, default_session
    from dagger.client._core import Context

    assert type(dagger.dag) is dagger_global.Client, type(dagger.dag)
    assert isinstance(dagger.dag, Session)
    assert dagger.dag is default_session()
    assert dagger.Container is core.Container
    assert dagger.Linter is linter.Linter
    assert dagger.Client is dagger_global.Client

    ctr = dagger.dag.container()
    assert type(ctr) is core.Container, type(ctr)
    assert ctr._ctx.conn is dagger.dag

    # Old and new calls mix: each takes what the other made.
    old = dagger.dag.directory()
    assert type(linter.linter(old)) is linter.Linter
    new = core.core().directory()
    assert new._ctx.conn is dagger.dag
    assert type(dagger.dag.linter(new)) is linter.Linter
    assert type(core.Binding(Context()).as_linter()) is linter.Linter
    print("ok")
"""


def test_global_client_makes_dag_the_client(generated: pathlib.Path):
    assert _run(WITH_GLOBAL_CLIENT, generated) == "ok\n"


CONNECTION_WITH_GLOBAL_CLIENT = """
    import anyio
    import dagger
    import dagger_global
    from dagger.client import default_session
    from dagger.provisioning import _connection

    class Engine:
        def get_shared_client_connection(self):
            return default_session().connection

        async def setup_client(self, conn):
            return conn

    class provision_engine:
        def __init__(self, cfg):
            pass

        async def __aenter__(self):
            return Engine()

        async def __aexit__(self, *_):
            pass

    _connection.provision_engine = provision_engine

    async def main():
        async with dagger.connection() as session:
            assert session is dagger.dag
            assert type(session) is dagger_global.Client

    anyio.run(main)
    print("ok")
"""


def test_connection_yields_the_global_client(generated: pathlib.Path):
    assert _run(CONNECTION_WITH_GLOBAL_CLIENT, generated) == "ok\n"


TYPED = """
    import dagger
    from dagger_clients.core import Container

    ctr: Container = dagger.dag.container()
    reveal_type(dagger.dag)
"""


@pytest.mark.slow
def test_mypy_types_dag_as_the_global_client(generated: pathlib.Path):
    (generated / "typed.py").write_text(textwrap.dedent(TYPED))

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--cache-dir",
            str(generated / ".mypy_cache"),
            str(generated / "typed.py"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "MYPYPATH": str(generated)},
    )

    # Notes and errors are not on one stream once mypy installs stubs.
    out = proc.stdout + proc.stderr
    assert 'Revealed type is "dagger_global.Client"' in out, out
    # Nothing else about the user's file, and nothing about the SDK's init.
    assert "typed.py:" not in out.replace("typed.py:6:13: note", ""), out
    assert "__init__.py" not in out, out
