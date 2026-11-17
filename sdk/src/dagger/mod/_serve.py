"""Serve a module's calls from one warm process, one call at a time.

The engine connects to PORT once per call and sends one frame, ``C``, with
the call. The server answers with ``O`` and ``E`` frames, the call's output
on file descriptors 1 and 2, then one ``R`` frame with the result JSON or one
``X`` frame with the error message, and closes the connection. A frame is a
one-byte kind, a four-byte big-endian payload length, and the payload.
"""

import base64
import contextlib
import json
import os
import pathlib
import selectors
import socket
import sys
import threading
from collections.abc import Iterator
from typing import Any

import anyio
from opentelemetry import context, propagate

from dagger import telemetry
from dagger.client import _session
from dagger.mod._exceptions import log_failure
from dagger.mod._module import Module

PORT = 8737
# Long enough to drain a call's last output, short enough that a process the
# call left running, which still holds the pipes, does not stall the server.
DRAIN_SECONDS = 1.0
# How soon a pump blocked on such a process notices that the call ended.
POLL_SECONDS = 0.05


def serve(mod: Module) -> None:
    """Listen only once the module is imported: a connection means ready."""
    telemetry.initialize()
    with socket.create_server(("127.0.0.1", PORT)) as listener:
        anyio.run(_serve_calls, mod, listener)


async def _serve_calls(mod: Module, listener: socket.socket) -> None:
    while True:
        conn, _ = await anyio.to_thread.run_sync(listener.accept)
        with conn:
            await serve_call(mod, conn)


async def serve_call(mod: Module, conn: socket.socket) -> None:
    """Run the call conn sends, as the client it names, and answer on conn."""
    with conn.makefile("rb") as f:
        head = f.read(5)
        call = json.loads(f.read(int.from_bytes(head[1:], "big")))
    metadata = base64.b64encode(json.dumps({"client_id": call["clientID"]}).encode())
    channel = _Channel(conn)
    environ, cwd = os.environ.copy(), pathlib.Path.cwd()
    _session.request_headers["X-Dagger-Client-Metadata"] = metadata.decode()
    token = context.attach(propagate.extract({"traceparent": call["traceparent"]}))
    try:
        with channel.capture():
            kind, payload = await _run(mod, call["request"])
    finally:
        context.detach(token)
        _session.request_headers.clear()
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(environ)
    channel.send(kind, payload, last=True)


async def _run(mod: Module, request: dict[str, Any]) -> tuple[bytes, bytes]:
    try:
        result = await mod.dispatch(request)
    except Exception as e:  # noqa: BLE001 - log_failure logs the traceback
        log_failure(e)
        return b"X", (str(e) or type(e).__name__).encode()
    finally:
        # The next call is another client, which has loaded nothing, so every
        # session must load again, including one a global holds.
        await _session.default_session().close()
        _session.forget_loads()
        telemetry.flush()
    return b"R", json.dumps(result).encode()


class _Channel:
    def __init__(self, conn: socket.socket):
        self._conn = conn
        self._lock = threading.Lock()
        self._open = True

    def send(self, kind: bytes, payload: bytes, *, last: bool = False) -> None:
        with self._lock:
            if self._open:
                self._conn.sendall(kind + len(payload).to_bytes(4, "big") + payload)
            self._open = self._open and not last

    @contextlib.contextmanager
    def capture(self) -> Iterator[None]:
        """Send fds 1 and 2 as frames, so subprocess output goes out too."""
        for stream in (sys.stdout, sys.stderr):
            stream.flush()
        saved = [os.dup(1), os.dup(2)]
        stop = threading.Event()
        pumps = [self._pump(1, b"O", stop), self._pump(2, b"E", stop)]
        try:
            yield
        finally:
            for stream in (sys.stdout, sys.stderr):
                stream.flush()
            for fd, old in zip((1, 2), saved, strict=True):
                os.dup2(old, fd)
                os.close(old)
            for pump in pumps:
                pump.join(DRAIN_SECONDS)
            stop.set()
            for pump in pumps:
                pump.join()

    def _pump(self, fd: int, kind: bytes, stop: threading.Event) -> threading.Thread:
        read, write = os.pipe()
        os.dup2(write, fd)
        os.close(write)
        pump = threading.Thread(target=self._forward, args=(read, kind, stop))
        pump.start()
        return pump

    def _forward(self, read: int, kind: bytes, stop: threading.Event) -> None:
        # A selector, not select(), which refuses descriptors from 1024 up.
        try:
            with selectors.DefaultSelector() as selector, contextlib.suppress(OSError):
                selector.register(read, selectors.EVENT_READ)
                while not stop.is_set():
                    if not selector.select(POLL_SECONDS):
                        continue
                    if not (chunk := os.read(read, 65536)):
                        break
                    self.send(kind, chunk)
        finally:
            os.close(read)
