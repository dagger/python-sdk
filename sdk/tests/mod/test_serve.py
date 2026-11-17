import base64
import fcntl
import json
import os
import pathlib
import resource
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
from opentelemetry import trace

from dagger.client import _load, _session
from dagger.client._descriptor import Target
from dagger.mod import Module, _serve
from dagger.mod._serve import serve_call

pytestmark = pytest.mark.anyio

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
GLOBAL_SESSION = _session.Session()
LINGERING: list[subprocess.Popen] = []


@pytest.fixture
def mod() -> Module:
    m = Module("Foo")

    @m.object_type
    class Foo:
        @m.function
        def talk(self) -> str:
            os.write(1, b"to stdout ")
            subprocess.run(
                [sys.executable, "-c", "import os; os.write(2, b'from a child')"],
                check=True,
            )
            return "said"

        @m.function
        def mess(self) -> dict[str, str]:
            os.environ["LEFT_BEHIND"] = "yes"
            os.chdir("/")
            span = trace.get_current_span().get_span_context()
            return {
                "header": _session.request_headers["X-Dagger-Client-Metadata"],
                "trace": format(span.trace_id, "032x"),
            }

        @m.function
        async def load(self) -> str:
            await GLOBAL_SESSION.load(Target("dep", "github.com/x/dep"))
            return "loaded"

        @m.function
        def linger(self) -> str:
            LINGERING.append(subprocess.Popen(["sleep", "30"]))
            return "left"

        @m.function
        def fail(self) -> str:
            msg = "broken"
            raise ValueError(msg)

    return m


async def _call(mod: Module, fn: str) -> list[tuple[bytes, bytes]]:
    call = json.dumps(
        {
            "request": {
                "receiverType": "Foo",
                "receiverValue": None,
                "fnName": fn,
                "fnArgs": "{}",
            },
            "clientID": "call-1",
            "traceparent": f"00-{TRACE_ID}-00f067aa0ba902b7-01",
        }
    ).encode()
    engine, server = socket.socketpair()
    with engine, server:
        engine.sendall(b"C" + len(call).to_bytes(4, "big") + call)
        await serve_call(mod, server)
        server.shutdown(socket.SHUT_WR)
        data = b"".join(iter(lambda: engine.recv(65536), b""))
    frames = []
    while data:
        size = int.from_bytes(data[1:5], "big")
        frames.append((data[:1], data[5 : 5 + size]))
        data = data[5 + size :]
    return frames


def _output(frames: list[tuple[bytes, bytes]], kind: bytes) -> bytes:
    return b"".join(payload for k, payload in frames if k == kind)


async def test_sends_output_then_result(mod: Module):
    frames = await _call(mod, "talk")
    assert _output(frames, b"O") == b"to stdout "
    assert _output(frames, b"E") == b"from a child"
    assert frames[-1] == (b"R", b'"said"')
    assert [k for k, _ in frames].count(b"R") == 1


async def test_binds_the_call_and_restores_the_process(
    mod: Module, monkeypatch: pytest.MonkeyPatch
):
    session = _session.default_session()
    forgotten = []
    monkeypatch.setattr(session, "forget", lambda: forgotten.append(session))
    cwd = pathlib.Path.cwd()

    kind, payload = (await _call(mod, "mess"))[-1]

    assert kind == b"R"
    result = json.loads(payload)
    assert json.loads(base64.b64decode(result["header"])) == {"client_id": "call-1"}
    assert result["trace"] == TRACE_ID
    assert _session.request_headers == {}
    assert "LEFT_BEHIND" not in os.environ
    assert pathlib.Path.cwd() == cwd
    assert forgotten == [session]
    assert _session.default_session() is session


async def test_error_keeps_serving(mod: Module):
    assert (await _call(mod, "fail"))[-1] == (b"X", b"broken")
    assert (await _call(mod, "talk"))[-1] == (b"R", b'"said"')


async def test_transport_sends_request_headers(monkeypatch: pytest.MonkeyPatch):
    sent = []

    async def handle(_, request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("X-Test"))
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    monkeypatch.setitem(_session.request_headers, "X-Test", "bound")
    transport = _session.TelemetryTransport()
    await transport.handle_async_request(httpx.Request("POST", "http://x/query"))
    assert sent == ["bound"]


async def test_every_session_loads_again(mod: Module, monkeypatch: pytest.MonkeyPatch):
    loaded = []

    async def load_target(session, target):
        loaded.append((session, target.name))

    monkeypatch.setattr(_load, "load_target", load_target)
    await _call(mod, "load")
    await _call(mod, "load")
    assert loaded == [(GLOBAL_SESSION, "dep")] * 2


def _open_fds() -> list[pathlib.Path]:
    return sorted(pathlib.Path("/proc/self/fd").iterdir())


async def test_process_left_running_releases_the_pumps(
    mod: Module, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(_serve, "DRAIN_SECONDS", 0.1)
    # The worker thread a sync function runs in outlives its call.
    await _call(mod, "talk")
    threads, fds = threading.active_count(), _open_fds()
    started = time.monotonic()
    try:
        assert (await _call(mod, "linger"))[-1] == (b"R", b'"left"')
        assert time.monotonic() - started < 5
        assert threading.active_count() == threads
        assert _open_fds() == fds
    finally:
        for proc in LINGERING:
            proc.kill()
            proc.wait()


async def test_output_from_a_descriptor_above_1024(
    mod: Module, monkeypatch: pytest.MonkeyPatch
):
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard != resource.RLIM_INFINITY and hard < 2048:
        pytest.skip("cannot open descriptors above 1024")
    pipe = os.pipe

    def high_pipe() -> tuple[int, int]:
        read, write = pipe()
        high = fcntl.fcntl(read, fcntl.F_DUPFD_CLOEXEC, 1024)
        os.close(read)
        return high, write

    resource.setrlimit(resource.RLIMIT_NOFILE, (2048, hard))
    try:
        with monkeypatch.context() as patched:
            patched.setattr(_serve.os, "pipe", high_pipe)
            frames = await _call(mod, "talk")
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    assert _output(frames, b"O") == b"to stdout "
    assert _output(frames, b"E") == b"from a child"
