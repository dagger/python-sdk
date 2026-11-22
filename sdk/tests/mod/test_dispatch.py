import json
import logging
import pathlib
import subprocess
import sys

import pytest

from dagger import QueryError
from dagger._exceptions import QueryErrorValue
from dagger.mod import Module
from dagger.mod._exceptions import (
    FUNCTION_ERROR_MARKER,
    FUNCTION_ERROR_PATH_ENV,
    ErrorOriginFilter,
    FunctionError,
    InvalidInputError,
    function_error_message,
    report_function_error,
    write_function_error,
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


def _call(
    module_dir: pathlib.Path,
    request: dict,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dagger.mod", "call", "--output", "out/result.json"],
        cwd=module_dir,
        env={
            "PATH": "",
            "PYTHONPATH": str(module_dir / "src"),
            "DAGGER_DEFAULT_PYTHON_PACKAGE": "hello",
            "DAGGER_MAIN_OBJECT": "Hello",
            **(env or {}),
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


def test_command_failure_writes_error_file(module_dir: pathlib.Path):
    request = {
        "receiverType": "Hello",
        "receiverValue": "{}",
        "fnName": "boom",
        "fnArgs": "{}",
    }
    error_file = module_dir / "function-error"
    proc = _call(module_dir, request, {FUNCTION_ERROR_PATH_ENV: str(error_file)})
    assert proc.returncode == 2
    report = json.loads(error_file.read_text())
    # The traceback names the type, so the error the engine shows needn't.
    assert report["message"] == "boom"
    assert report["values"]["exception.type"] == "RuntimeError"
    assert report["values"]["exception.message"] == "boom"
    assert "raise RuntimeError('boom')" in report["values"]["exception.stacktrace"]
    assert "RuntimeError: boom" in proc.stderr
    assert FUNCTION_ERROR_MARKER not in proc.stderr
    # The engine shows the error; the process doesn't echo it.
    echoes = [
        line
        for line in proc.stderr.splitlines()
        if line.strip() == "boom" or line.endswith("]: boom")
    ]
    assert not echoes, proc.stderr


def test_command_success_writes_no_error_file(module_dir: pathlib.Path):
    request = {
        "receiverType": "Hello",
        "receiverValue": "{}",
        "fnName": "hi",
        "fnArgs": '{"who": "you"}',
    }
    error_file = module_dir / "function-error"
    proc = _call(module_dir, request, {FUNCTION_ERROR_PATH_ENV: str(error_file)})
    assert proc.returncode == 0, proc.stderr
    assert not error_file.exists()


def test_error_origin_filter_strips_origins_from_logs():
    try:
        raise _query_error("exit code: 1" + ORIGIN)
    except QueryError as e:
        record = logging.LogRecord(
            "dagger.mod", logging.ERROR, __file__, 1, "failed: %s", (e,), sys.exc_info()
        )
    assert ErrorOriginFilter().filter(record)
    formatted = logging.Formatter().format(record)
    assert "traceparent" not in formatted, formatted
    assert "failed: exit code: 1" in formatted
    assert formatted.rstrip().endswith("exit code: 1")


def _exec_error(message: str) -> QueryError:
    return QueryError(
        [
            QueryErrorValue(
                message,
                extensions={
                    "_type": "EXEC_ERROR",
                    "cmd": ["sh", "-c", "exit 7"],
                    "exitCode": 7,
                    "stdout": "out",
                    "stderr": "err",
                },
            )
        ],
        "query",
    )


def _report(tmp_path: pathlib.Path, exc: BaseException) -> dict:
    error_file = tmp_path / "function-error"
    write_function_error(str(error_file), exc)
    return json.loads(error_file.read_text())


def test_write_function_error_keeps_an_api_error_whole(tmp_path: pathlib.Path):
    message = "\x01" * 100_000 + "exit code: 7" + ORIGIN
    report = _report(tmp_path, _exec_error(message))
    assert report["message"] == message, "nothing is trimmed, the origin kept"
    values = report["values"]
    assert values["_type"] == "EXEC_ERROR"
    assert values["exitCode"] == 7
    assert values["cmd"] == ["sh", "-c", "exit 7"]
    assert (values["stdout"], values["stderr"]) == ("out", "err")
    assert values["exception.type"] == "dagger.ExecError"


def test_write_function_error_of_a_wrapped_api_error(tmp_path: pathlib.Path):
    try:
        try:
            raise _exec_error("exit code: 7" + ORIGIN)
        except QueryError as e:
            msg = f"it ran a failing command: {e}"
            raise RuntimeError(msg) from e
    except RuntimeError as e:
        report = _report(tmp_path, _wrapped(e))
    assert report["message"] == "it ran a failing command: exit code: 7" + ORIGIN
    assert report["values"]["exitCode"] == 7
    assert report["values"]["exception.type"] == "RuntimeError"


def test_write_function_error_of_a_module_error(tmp_path: pathlib.Path):
    exc = InvalidInputError("bad input", extra={"field": "who", "bad": object()})
    report = _report(tmp_path, exc)
    assert report["message"] == "bad input"
    assert report["values"]["extra.field"] == "who"
    assert report["values"]["extra.bad"].startswith("<object object")
    assert report["values"]["exception.type"] == "InvalidInputError"


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


def test_function_error_message_of_a_logged_error():
    assert function_error_message(_wrapped(ValueError("boom")), logged=True) == "boom"
    assert function_error_message(_wrapped(ValueError()), logged=True) == "ValueError"


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
