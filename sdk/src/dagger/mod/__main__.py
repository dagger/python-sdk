"""Commands the generated entrypoint runs in the module's container."""

import argparse
import json
import logging
import pathlib
import sys
from typing import Any

import anyio

import dagger
from dagger import telemetry
from dagger.mod._exceptions import ModuleError

logger = logging.getLogger(__package__)


def main(argv: list[str] | None = None) -> int:
    """Run one command and return the exit status."""
    parser = argparse.ArgumentParser(prog="python -m dagger.mod")
    commands = parser.add_subparsers(required=True)

    entrypoint = commands.add_parser(
        "entrypoint",
        help="render the static entrypoint of the module in the current directory",
    )
    entrypoint.add_argument("--name", required=True, help="module name")
    entrypoint.add_argument(
        "--path", required=True, help="module directory, relative to the workspace"
    )
    entrypoint.add_argument("--output", required=True, type=pathlib.Path)
    entrypoint.set_defaults(run=_entrypoint)

    call = commands.add_parser(
        "call",
        help="run the call read from standard input and write its JSON result",
    )
    call.add_argument("--output", required=True, type=pathlib.Path)
    call.set_defaults(run=_call)

    args = parser.parse_args(argv)
    try:
        args.run(args)
    except (ModuleError, dagger.QueryError) as e:
        logger.error(str(e))  # noqa: TRY400 - the message is the whole story
        return 2
    except Exception:
        logger.exception("Unhandled exception")
        return 1
    return 0


def _entrypoint(args: argparse.Namespace) -> None:
    from dagger.mod._entrypoint import write_entrypoint
    from dagger.mod.cli import load_module

    write_entrypoint(
        load_module().describe(),
        name=args.name,
        path=args.path,
        root=pathlib.Path.cwd(),
        output=args.output,
    )


def _call(args: argparse.Namespace) -> None:
    request = json.load(sys.stdin)
    telemetry.initialize()
    try:
        result = anyio.run(_dispatch, request)
    finally:
        telemetry.shutdown()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result))


async def _dispatch(request: dict[str, Any]) -> Any:
    from dagger.mod.cli import load_module

    return await load_module().dispatch(request)


if __name__ == "__main__":
    sys.exit(main())
