import json
import pathlib
import subprocess
import sys

import pytest

from dagger.mod import Module
from dagger.mod._exceptions import InvalidInputError

pytestmark = pytest.mark.anyio


@pytest.fixture
def mod() -> Module:
    m = Module("Foo")

    @m.object_type
    class Foo:
        name: str = m.field(default="foo")

        @m.function
        def hello(self, who: str | None = None, times: int = 1) -> str:
            return (who if who is not None else self.name) * times

    return m


async def test_constructor(mod: Module):
    request = {
        "receiverType": "Foo",
        "receiverValue": None,
        "fnName": "",
        "fnArgs": "{}",
    }
    assert await mod.dispatch(request) == {"name": "foo"}


async def test_function_with_receiver_state(mod: Module):
    request = {
        "receiverType": "Foo",
        "receiverValue": '{"name": "bar"}',
        "fnName": "hello",
        "fnArgs": '{"times": 2}',
    }
    assert await mod.dispatch(request) == "barbar"


async def test_arguments_are_decoded_once(mod: Module):
    request = {
        "receiverType": "Foo",
        "receiverValue": "{}",
        "fnName": "hello",
        "fnArgs": '{"who": "null"}',
    }
    assert await mod.dispatch(request) == "null"


async def test_null_argument(mod: Module):
    request = {
        "receiverType": "Foo",
        "receiverValue": "{}",
        "fnName": "hello",
        "fnArgs": '{"who": null}',
    }
    assert await mod.dispatch(request) == "foo"


async def test_malformed_request(mod: Module):
    request = {
        "receiverType": "Foo",
        "receiverValue": "{",
        "fnName": "hello",
        "fnArgs": "{}",
    }
    with pytest.raises(InvalidInputError, match="decode the call request"):
        await mod.dispatch(request)


@pytest.fixture
def module_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "hello"\n')
    pkg = tmp_path / "src" / "hello"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "from dagger import function, object_type\n\n"
        "@object_type\nclass Hello:\n"
        "    @function\n"
        "    def hi(self, who: str) -> str:\n"
        "        return 'hi ' + who\n"
        "    @function\n"
        "    def boom(self) -> str:\n"
        "        raise RuntimeError('boom')\n"
    )
    return tmp_path


def _call(module_dir: pathlib.Path, request: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dagger.mod", "call", "--output", "out/result.json"],
        cwd=module_dir,
        env={
            "PATH": "",
            "PYTHONPATH": str(module_dir / "src"),
            "DAGGER_DEFAULT_PYTHON_PACKAGE": "hello",
            "DAGGER_MAIN_OBJECT": "Hello",
        },
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=False,
    )


def test_command_writes_result(module_dir: pathlib.Path):
    request = {
        "receiverType": "Hello",
        "receiverValue": "{}",
        "fnName": "hi",
        "fnArgs": '{"who": "you"}',
    }
    proc = _call(module_dir, request)
    assert proc.returncode == 0, proc.stderr
    assert json.loads((module_dir / "out" / "result.json").read_text()) == "hi you"


def test_command_failure_writes_nothing(module_dir: pathlib.Path):
    request = {
        "receiverType": "Hello",
        "receiverValue": "{}",
        "fnName": "boom",
        "fnArgs": "{}",
    }
    proc = _call(module_dir, request)
    assert proc.returncode == 2
    assert "boom" in proc.stderr
    assert not (module_dir / "out").exists()
