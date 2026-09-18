"""What module code can read of its caller's files: nothing it was not handed.

Copied into a module that runs on an entrypoint. `reach` takes every ID the
SDK's loader holds, however it holds it, and tries each as a workspace, as a
module source and as a module: every way an ID of the caller's could lead back
to a caller file. Then the module's own current workspace. It returns how many
IDs it tried and what it read.
"""

from dagger.client import _load
from dagger.client._core import Arg, Context


def _held_ids() -> list[str]:
    found: list[str] = []

    def walk(value, depth=0):
        if depth > 4:
            return
        if isinstance(value, str) and len(value) > 20:
            found.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v, depth + 1)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for v in value:
                walk(v, depth + 1)
        elif hasattr(value, "__dict__") and not isinstance(value, type):
            walk(vars(value), depth + 1)

    for name, value in vars(_load).items():
        if name.startswith("_") and not name.startswith("__"):
            walk(value)
    return found


def _file(ctx: Context, path: str) -> Context:
    return ctx.select("Directory", "file", [Arg("path", path)]).select(
        "File", "contents", []
    )


def _attempts(held: str, secret: str) -> dict[str, Context]:
    attempts = {
        "workspace": Context()
        .select_id("Workspace", held)
        .select("Workspace", "file", [Arg("path", "/" + secret)])
        .select("File", "contents", []),
    }
    sources = {
        "source": Context().select_id("ModuleSource", held),
        "module.source": Context()
        .select_id("Module", held)
        .select("Module", "source", []),
    }
    for label, source in sources.items():
        # A source keeps the workspace it was loaded from, and reloads its
        # context from it for more includes; `..` climbs its context.
        attempts[label + ".withIncludes"] = _file(
            source.select(
                "ModuleSource", "withIncludes", [Arg("patterns", ["../" * 8 + secret])]
            ).select("ModuleSource", "contextDirectory", []),
            secret,
        )
        attempts[label + ".directory"] = _file(
            source.select("ModuleSource", "directory", [Arg("path", "../" * 8)]),
            secret,
        )
    return attempts


async def _read(ctx: Context) -> str | None:
    try:
        return await ctx.execute(str)
    except Exception:  # noqa: BLE001 - any failure is a read that did not happen
        return None


async def reach(secret: str) -> str:
    held = _held_ids()
    reads = []
    for i in held:
        for label, ctx in _attempts(i, secret).items():
            if (got := await _read(ctx)) is not None:
                reads.append(f"{label}: {got.strip()}")
    own = Context().root_select("currentWorkspace", [])
    got = await _read(_file(own.select("Workspace", "directory", [Arg("path", "/")]), secret))
    if got is not None:
        reads.append(f"currentWorkspace: {got.strip()}")
    return f"held={len(held)} reads={reads}"
