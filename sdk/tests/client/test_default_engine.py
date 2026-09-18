"""The default session provisions an engine in a plain program.

Each program runs in its own interpreter: the shared connection is a
singleton, and closing at exit only happens when an interpreter exits. A fake
CLI stands in for `dagger session`: it prints connection params, and when its
stdin closes it waits a little before writing that it ended, so a program
that did not wait for it exits before the mark is there.
"""

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

FAKE_CLI = """\
#!{python}
import pathlib, sys, time
marks = pathlib.Path({marks!r})
with (marks / "started").open("a") as f:
    f.write("session\\n")
print('{{"port": 4242, "session_token": "fake"}}', flush=True)
sys.stdin.read()
time.sleep(0.5)
(marks / "ended").write_text("ended")
"""


@pytest.fixture
def marks(tmp_path: pathlib.Path) -> pathlib.Path:
    cli = tmp_path / "dagger"
    cli.write_text(FAKE_CLI.format(python=sys.executable, marks=str(tmp_path)))
    cli.chmod(0o755)
    return tmp_path


def run(program: str, marks: pathlib.Path, **env: str) -> subprocess.CompletedProcess:
    environ = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DAGGER_SESSION_PORT", "DAGGER_SESSION_TOKEN")
    }
    environ["_EXPERIMENTAL_DAGGER_CLI_BIN"] = str(marks / "dagger")
    environ.update(env)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(program)],
        capture_output=True,
        text=True,
        env=environ,
        timeout=60,
        check=False,
    )


READY = """
import anyio
from dagger.client._session import SharedConnection

async def main():
    session = await SharedConnection()._ready()
    print(session.conn.port)

anyio.run(main)
"""


def test_a_plain_program_provisions_and_ends_the_engine_at_exit(marks):
    proc = run(READY, marks)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "4242"
    assert (marks / "started").read_text() == "session\n"
    # Written after the CLI's stdin closed and a pause: the program waited.
    assert (marks / "ended").exists()


def test_a_session_in_the_environment_is_never_provisioned(marks):
    proc = run(READY, marks, DAGGER_SESSION_PORT="5151", DAGGER_SESSION_TOKEN="t")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "5151"
    assert not (marks / "started").exists()


def test_without_provisioning_there_is_no_session(marks):
    # A module's runtime has no dagger.provisioning.
    proc = run(
        """
        import importlib.abc, sys

        class NoProvisioning(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name.startswith("dagger.provisioning"):
                    raise ModuleNotFoundError(name, name=name)

        sys.meta_path.insert(0, NoProvisioning())
        """
        + textwrap.indent(READY, "        "),
        marks,
    )

    assert proc.returncode == 1
    assert "No active engine session to connect to" in proc.stderr
    assert not (marks / "started").exists()


def test_concurrent_first_queries_provision_once(marks):
    proc = run(
        """
        import anyio
        from dagger.client._session import SharedConnection

        async def main():
            async with anyio.create_task_group() as tg:
                for _ in range(3):
                    tg.start_soon(SharedConnection()._ready)

        anyio.run(main)
        """,
        marks,
    )

    assert proc.returncode == 0, proc.stderr
    assert (marks / "started").read_text().count("session") == 1


def test_close_ends_the_engine_before_it_returns(marks):
    proc = run(
        """
        import os, pathlib, anyio, dagger
        from dagger.client._session import SharedConnection

        marks = pathlib.Path(os.environ["_EXPERIMENTAL_DAGGER_CLI_BIN"]).parent

        async def main():
            await SharedConnection()._ready()
            await dagger.close()
            print((marks / "ended").exists())

        anyio.run(main)
        """,
        marks,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "True"


MODULE_ENTRYPOINTS = {
    # The runtime executable's entrypoint.
    "cli": """
        import dagger.mod.cli as cli
        from dagger.client._session import SharedConnection

        async def main(mod=None, register=False):
            await SharedConnection()._ready()

        cli.main = main
        cli.app()
        """,
    # The generated entrypoint's commands.
    "python -m dagger.mod": """
        import anyio
        import dagger.mod.__main__ as entry
        from dagger.client._session import SharedConnection

        def call(args):
            anyio.run(SharedConnection()._ready)

        entry._call = call
        entry.main(["call", "--output", "/dev/null"])
        """,
}


@pytest.mark.parametrize("entry", MODULE_ENTRYPOINTS)
def test_a_module_never_provisions(marks, entry):
    # Not even when its session is missing from the environment.
    proc = run(MODULE_ENTRYPOINTS[entry], marks)

    assert "No active engine session to connect to" in proc.stderr
    assert not (marks / "started").exists()
