import json
import pathlib
import subprocess
import sys

import pytest

from dagger import QueryError
from dagger._exceptions import QueryErrorValue
from dagger.mod import Module
from dagger.mod._exceptions import (
    FUNCTION_ERROR_MARKER,
    FunctionError,
    InvalidInputError,
    function_error_message,
    report_function_error,
)

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


def test_command_failure_reports_error_last(module_dir: pathlib.Path):
    request = {
        "receiverType": "Hello",
        "receiverValue": "{}",
        "fnName": "boom",
        "fnArgs": "{}",
    }
    proc = _call(module_dir, request)
    last = proc.stderr.rstrip("\n").split("\n")[-1]
    assert last.startswith(FUNCTION_ERROR_MARKER), proc.stderr
    assert json.loads(last.removeprefix(FUNCTION_ERROR_MARKER)) == "RuntimeError: boom"


ORIGIN = " [traceparent:" + "a" * 32 + "-" + "b" * 16 + "]"


def _query_error(message: str) -> QueryError:
    return QueryError([QueryErrorValue(message)], "query")


@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (_query_error("exit code: 3" + ORIGIN), "exit code: 3" + ORIGIN),
        (InvalidInputError("bad input"), "bad input"),
        (KeyError("k"), "KeyError: 'k'"),
        (ValueError(), "ValueError"),
        (FunctionError(""), "FunctionError"),
    ],
)
def test_function_error_message(exc: BaseException, message: str):
    assert function_error_message(exc) == message


def _wrapped(cause: BaseException) -> FunctionError:
    # As Module.call wraps what a function raises.
    wrapper = FunctionError(str(cause))
    wrapper.__cause__ = cause
    return wrapper


def test_function_error_message_names_the_function_error():
    assert function_error_message(_wrapped(ValueError("boom"))) == "ValueError: boom"


def test_function_error_message_reports_a_group_error_by_error():
    group = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [_query_error("exit code: 3" + ORIGIN), KeyError("k")],
    )
    assert function_error_message(_wrapped(group)) == (
        "exit code: 3" + ORIGIN + "; KeyError: 'k'"
    )


def test_report_keeps_origin(capsys: pytest.CaptureFixture[str]):
    report_function_error("exit code: 1\nline two" + ORIGIN)
    last = capsys.readouterr().err.rstrip("\n").split("\n")[-1]
    reported = json.loads(last.removeprefix(FUNCTION_ERROR_MARKER))
    assert reported == "exit code: 1\nline two" + ORIGIN


def test_report_truncates_but_keeps_origin(capsys: pytest.CaptureFixture[str]):
    report_function_error("\x01" * 100_000 + ORIGIN)
    err = capsys.readouterr().err
    last = err.rstrip("\n").split("\n")[-1]
    assert len(last.encode()) < 100 << 10
    reported = json.loads(last.removeprefix(FUNCTION_ERROR_MARKER))
    assert reported.endswith("..." + ORIGIN)
