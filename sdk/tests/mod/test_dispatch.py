import importlib
import json
import logging
import os
import pathlib
import subprocess
import sys

import pytest

from dagger import QueryError
from dagger._exceptions import QueryErrorValue
from dagger.mod import Module, _exceptions
from dagger.mod._exceptions import (
    FUNCTION_ERROR_MARKER,
    FUNCTION_ERROR_PATH_ENV,
    ErrorOriginFilter,
    FunctionError,
    InvalidInputError,
    errors_to_show,
    format_user_traceback,
    function_error_message,
    has_error_origin,
    report_function_error,
    write_function_error,
)

pytestmark = pytest.mark.anyio

ORIGIN = " [traceparent:" + "a" * 32 + "-" + "b" * 16 + "]"


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
        "    @function\n"
        "    def api_boom(self, origin: str = '') -> str:\n"
        "        from dagger import QueryError\n"
        "        from dagger._exceptions import QueryErrorValue\n"
        "        ext = {'_type': 'EXEC_ERROR', 'cmd': ['false'], 'exitCode': 1,\n"
        "               'stdout': 'out', 'stderr': 'err'}\n"
        "        err = QueryErrorValue('exit code: 1' + origin, extensions=ext)\n"
        "        raise QueryError([err], 'query')\n"
        "    @function\n"
        "    def group_boom(self, origin: str = '') -> str:\n"
        "        from dagger import QueryError\n"
        "        from dagger._exceptions import QueryErrorValue\n"
        "        err = QueryError([QueryErrorValue('exit code: 1' + origin)], 'q')\n"
        "        raise ExceptionGroup('unhandled errors in a TaskGroup', [err])\n"
        "    @function\n"
        "    def sdk_boom(self) -> str:\n"
        "        return _sdk_boom()\n"
        "    @function\n"
        "    def wrapped_boom(self) -> str:\n"
        "        try:\n"
        "            _sdk_boom()\n"
        "        except ValueError as e:\n"
        "            raise RuntimeError(f'wrapped: {e}') from e\n"
        "\n"
        "def _sdk_boom():\n"
        "    from dagger import QueryError\n"
        "    # The SDK raises: an API error needs an error.\n"
        "    return QueryError([], 'query')\n"
    )
    return tmp_path


@pytest.fixture
def broken_module_dir(module_dir: pathlib.Path) -> pathlib.Path:
    (module_dir / "src" / "hello" / "__init__.py").write_text(
        "import json\n\nthis is not python\n"
    )
    return module_dir


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


def _request(fn: str, args: dict | None = None) -> dict:
    return {
        "receiverType": "Hello",
        "receiverValue": "{}",
        "fnName": fn,
        "fnArgs": json.dumps(args or {}),
    }


def _call_with_error_file(
    module_dir: pathlib.Path, request: dict
) -> tuple[subprocess.CompletedProcess, dict]:
    error_file = module_dir / "function-error"
    proc = _call(module_dir, request, {FUNCTION_ERROR_PATH_ENV: str(error_file)})
    return proc, json.loads(error_file.read_text())


def _sdk_frames(stderr: str) -> list[str]:
    return [
        line
        for line in stderr.splitlines()
        if line.lstrip().startswith("File ")
        and ("/dagger/" in line or "importlib" in line or "<frozen" in line)
    ]


def test_command_failure_shows_short_traceback_with_error_file(
    module_dir: pathlib.Path,
):
    proc, report = _call_with_error_file(module_dir, _request("boom"))
    assert proc.returncode == 2
    assert proc.stderr.startswith("Traceback (most recent call last):\n"), proc.stderr
    assert "in boom\n    raise RuntimeError('boom')\n" in proc.stderr
    assert proc.stderr.endswith("\nRuntimeError: boom\n"), proc.stderr
    assert not _sdk_frames(proc.stderr), proc.stderr
    assert "Unhandled exception" not in proc.stderr
    # The report keeps the whole traceback.
    assert "_module.py" in report["values"]["exception.stacktrace"]


def test_command_failure_shows_full_traceback_without_error_file(
    module_dir: pathlib.Path,
):
    proc = _call(module_dir, _request("boom"))
    assert proc.returncode == 2
    assert "Unhandled exception while executing function" in proc.stderr
    assert "_module.py" in proc.stderr


def test_command_failure_in_the_sdk_shows_the_users_frames(
    module_dir: pathlib.Path,
):
    proc, report = _call_with_error_file(module_dir, _request("sdk_boom"))
    assert proc.returncode == 2
    assert "in sdk_boom\n" in proc.stderr
    assert "in _sdk_boom\n" in proc.stderr
    assert proc.stderr.endswith("\nValueError: Errors list is empty\n"), proc.stderr
    assert not _sdk_frames(proc.stderr), proc.stderr
    assert "dagger/_exceptions.py" in report["values"]["exception.stacktrace"]


def test_command_chained_failure_shows_short_tracebacks(module_dir: pathlib.Path):
    proc, _ = _call_with_error_file(module_dir, _request("wrapped_boom"))
    assert proc.returncode == 2
    assert proc.stderr.count("Traceback (most recent call last):") == 2
    cause, _, effect = proc.stderr.partition(
        "\nThe above exception was the direct cause of the following exception:\n"
    )
    assert "in _sdk_boom\n" in cause
    assert cause.rstrip().endswith("ValueError: Errors list is empty"), cause
    assert "in wrapped_boom\n    raise RuntimeError" in effect
    assert effect.endswith("\nRuntimeError: wrapped: Errors list is empty\n"), effect
    assert not _sdk_frames(proc.stderr), proc.stderr


def test_command_import_error_shows_short_traceback_with_error_file(
    broken_module_dir: pathlib.Path,
):
    proc, report = _call_with_error_file(broken_module_dir, _request("hi"))
    assert proc.returncode == 2
    assert proc.stderr.startswith("Traceback (most recent call last):\n"), proc.stderr
    assert "in <module>\n    this is not python\n" in proc.stderr
    assert "NameError: name 'this' is not defined" in proc.stderr
    assert not _sdk_frames(proc.stderr), proc.stderr
    assert "Error while importing" not in proc.stderr
    assert report["message"] == "name 'this' is not defined"


def test_command_import_error_logs_traceback_without_error_file(
    broken_module_dir: pathlib.Path,
):
    proc = _call(broken_module_dir, _request("hi"))
    assert proc.returncode == 2
    assert "Error while importing Python module 'hello'" in proc.stderr
    assert "cli.py" in proc.stderr


def test_command_api_error_logs_nothing_with_error_file(module_dir: pathlib.Path):
    proc, report = _call_with_error_file(
        module_dir, _request("api_boom", {"origin": ORIGIN})
    )
    assert proc.returncode == 2
    # The engine shows the call that failed, with its logs.
    assert proc.stderr == "", proc.stderr
    assert report["message"] == "exit code: 1" + ORIGIN
    values = report["values"]
    assert values["_type"] == "EXEC_ERROR"
    assert values["exitCode"] == 1
    assert values["exception.type"] == "dagger.ExecError"
    assert "raise QueryError" in values["exception.stacktrace"]


def test_command_api_error_without_origin_shows_short_traceback(
    module_dir: pathlib.Path,
):
    proc, report = _call_with_error_file(module_dir, _request("api_boom"))
    assert proc.returncode == 2
    # No call the engine shows it at: the function shows where it got it.
    assert proc.stderr.startswith("Traceback (most recent call last):\n"), proc.stderr
    assert "in api_boom\n    raise QueryError([err], 'query')\n" in proc.stderr
    assert proc.stderr.endswith("\ndagger.ExecError: exit code: 1\n"), proc.stderr
    assert not _sdk_frames(proc.stderr), proc.stderr
    assert "API error while executing function" not in proc.stderr
    assert report["message"] == "exit code: 1"


def test_command_group_of_api_errors_logs_nothing_with_error_file(
    module_dir: pathlib.Path,
):
    proc, report = _call_with_error_file(
        module_dir, _request("group_boom", {"origin": ORIGIN})
    )
    assert proc.returncode == 2
    # As a task group raises them: the engine shows where each comes from.
    assert proc.stderr == "", proc.stderr
    assert report["message"] == "exit code: 1" + ORIGIN


def test_command_group_of_api_errors_without_origin_shows_them(
    module_dir: pathlib.Path,
):
    proc, _ = _call_with_error_file(module_dir, _request("group_boom"))
    assert proc.returncode == 2
    assert "in group_boom\n" in proc.stderr, proc.stderr
    assert "QueryError: exit code: 1" in proc.stderr, proc.stderr
    assert not _sdk_frames(proc.stderr), proc.stderr


def test_command_api_error_logs_traceback_without_error_file(
    module_dir: pathlib.Path,
):
    proc = _call(module_dir, _request("api_boom", {"origin": ORIGIN}))
    assert proc.returncode == 2
    assert "API error while executing function" in proc.stderr
    assert "Traceback" in proc.stderr
    assert "dagger.ExecError: exit code: 1" in proc.stderr
    last = proc.stderr.rstrip("\n").split("\n")[-1]
    reported = json.loads(last.removeprefix(FUNCTION_ERROR_MARKER))
    assert reported == "exit code: 1" + ORIGIN


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


def test_user_traceback_leaves_out_library_frames():
    try:
        json.loads("{")
    except ValueError as e:
        formatted = format_user_traceback(e)
    assert 'json.loads("{")' in formatted, formatted
    assert "decoder.py" not in formatted, formatted
    assert formatted.rstrip().endswith("line 1 column 2 (char 1)"), formatted


def test_user_traceback_leaves_out_error_origins():
    try:
        raise _query_error("exit code: 1" + ORIGIN)
    except QueryError as e:
        formatted = format_user_traceback(e)
    assert "traceparent" not in formatted, formatted
    assert formatted.endswith("QueryError: exit code: 1\n"), formatted


def test_user_traceback_leaves_out_generated_code(tmp_path: pathlib.Path):
    generated = tmp_path / "client.py"
    generated.write_text(
        "# Code generated by dagger. DO NOT EDIT.\n\n"
        "def call():\n"
        "    raise ValueError('from a client')\n"
    )
    namespace: dict = {}
    exec(compile(generated.read_text(), str(generated), "exec"), namespace)
    try:
        namespace["call"]()
    except ValueError as e:
        formatted = format_user_traceback(e)
    assert 'namespace["call"]()' in formatted, formatted
    assert "client.py" not in formatted, formatted


def _installed_package(
    site_dir: pathlib.Path, name: str, module: str, source: str, *, local: bool
) -> None:
    """Install a distribution of one module of the tb_fixture_ns namespace."""
    file = f"tb_fixture_ns/{module}.py"
    (site_dir / file).parent.mkdir(parents=True, exist_ok=True)
    (site_dir / file).write_text(source)
    info = site_dir / f"{name}-0.1.dist-info"
    info.mkdir()
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.1\n"
    (info / "METADATA").write_text(metadata)
    (info / "RECORD").write_text(f"{file},,\n")
    if local:
        url = (site_dir.parent / name).as_uri()
        (info / "direct_url.json").write_text(json.dumps({"url": url, "dir_info": {}}))


@pytest.fixture
def site_dir(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    monkeypatch.syspath_prepend(str(site_dir))
    installed = (os.path.realpath(site_dir) + os.sep,)
    monkeypatch.setattr(_exceptions, "_site_dirs", lambda: installed)
    _exceptions._local_files.cache_clear()
    yield site_dir
    _exceptions._local_files.cache_clear()
    for name in list(sys.modules):
        if name.startswith("tb_fixture_"):
            del sys.modules[name]


def test_user_traceback_keeps_the_users_installed_libraries(site_dir: pathlib.Path):
    # The runtime installs the module's own libraries from their directories,
    # not editable, beside the packages from an index, even in one namespace.
    _installed_package(
        site_dir,
        "tb_fixture_mine",
        "mine",
        "def parse(s):\n    raise ValueError('empty: ' + s)\n",
        local=True,
    )
    _installed_package(
        site_dir,
        "tb_fixture_theirs",
        "theirs",
        "def run(fn, s):\n    fn(s)\n",
        local=False,
    )
    importlib.invalidate_caches()
    mine = importlib.import_module("tb_fixture_ns.mine")
    theirs = importlib.import_module("tb_fixture_ns.theirs")
    try:
        theirs.run(mine.parse, "x")
    except ValueError as e:
        formatted = format_user_traceback(e)
    assert "theirs.run(mine.parse" in formatted, formatted
    assert "tb_fixture_ns/mine.py" in formatted, formatted
    assert "raise ValueError('empty: ' + s)" in formatted, formatted
    assert "theirs.py" not in formatted, formatted


def test_errors_to_show():
    shown = _query_error("no such ID")
    assert errors_to_show(_query_error("exit code: 1" + ORIGIN)) is None
    assert errors_to_show(shown) is shown
    assert errors_to_show(KeyError("k")) is not None
    group = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [_query_error("exit code: 1" + ORIGIN), KeyError("k"), shown],
    )
    rest = errors_to_show(group)
    assert isinstance(rest, ExceptionGroup)
    assert [type(e) for e in rest.exceptions] == [KeyError, QueryError]
    only_origins = ExceptionGroup("g", [_query_error("exit code: 1" + ORIGIN)])
    assert errors_to_show(only_origins) is None


def test_errors_to_show_keeps_a_group_that_loses_nothing():
    class ValidationErrors(ExceptionGroup):
        pass

    group = ValidationErrors("bad input", [KeyError("k"), _query_error("no such ID")])
    assert errors_to_show(group) is group, "the user's own group type"


def test_user_traceback_without_user_frames():
    exc = ValueError("boom")
    assert format_user_traceback(exc) == "ValueError: boom\n"


def test_user_traceback_of_a_group():
    def sdk_error() -> BaseException:
        try:
            QueryError([], "query")
        except ValueError as e:
            return e
        raise AssertionError

    group = ExceptionGroup("unhandled errors in a TaskGroup", [sdk_error()])
    formatted = format_user_traceback(group)
    assert "in sdk_error" in formatted, formatted
    assert "dagger/_exceptions.py" not in formatted, formatted
    assert "ValueError: Errors list is empty" in formatted, formatted


@pytest.mark.parametrize(
    ("message", "expected"),
    [("exit code: 1" + ORIGIN, True), ("no such ID", False)],
)
def test_has_error_origin(message: str, expected: bool):
    assert has_error_origin(_query_error(message)) is expected


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
    message = "x" * 100_000 + "exit code: 7" + ORIGIN
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


def test_write_function_error_of_an_error_raised_handling_an_api_error(
    tmp_path: pathlib.Path,
):
    try:
        try:
            raise _exec_error("exit code: 7" + ORIGIN)
        except QueryError:
            msg = "lint failed"
            raise RuntimeError(msg)  # noqa: B904
    except RuntimeError as e:
        report = _report(tmp_path, _wrapped(e))
    assert report["message"] == "lint failed"
    assert "_type" not in report["values"], "not the handled exec's error"
    assert report["values"]["exception.type"] == "RuntimeError"


def test_write_function_error_of_a_function_error_with_extra(
    tmp_path: pathlib.Path,
):
    try:
        try:
            raise _exec_error("exit code: 7" + ORIGIN)
        except QueryError as e:
            msg = "deploy failed"
            raise FunctionError(msg, extra={"env": "prod"}) from e
    except FunctionError as e:
        report = _report(tmp_path, e)
    assert report["values"]["extra.env"] == "prod"
    assert report["values"]["exitCode"] == 7


def test_write_function_error_over_the_limit(tmp_path: pathlib.Path):
    message = "x" * (1 << 19) + ORIGIN
    error_file = tmp_path / "function-error"
    write_function_error(str(error_file), _exec_error(message))
    assert len(error_file.read_bytes()) <= 1 << 20, "the engine reads it"
    report = json.loads(error_file.read_text())
    assert report["message"].endswith("..." + ORIGIN), "the origin kept"
    assert report["values"]["exitCode"] == 7
    assert "exception.stacktrace" not in report["values"]


def test_write_function_error_of_a_module_error(tmp_path: pathlib.Path):
    exc = InvalidInputError("bad input", extra={"field": "who", "bad": object()})
    report = _report(tmp_path, exc)
    assert report["message"] == "bad input"
    assert report["values"]["extra.field"] == "who"
    assert report["values"]["extra.bad"].startswith("<object object")
    assert report["values"]["exception.type"] == "InvalidInputError"


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
