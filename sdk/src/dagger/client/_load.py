"""How the module a target names gets served into a session."""

from dagger._exceptions import QueryError
from dagger.client._core import Arg, Context
from dagger.client._descriptor import Target, missing_field
from dagger.client._session import Session

# A descriptor has one shape for a git ref and for a workspace path: `ref` is
# the address, `pin` the refPin. There is no name: the engine derives it from
# the module's own config, as it did for the schema this client was generated
# from, so the two agree unless the module renamed itself since, and then the
# client is stale and its first selection says so (see stale_client_error).
#
# Two queries serve a target, and one condition picks between them:
#
#   a local target, in a process a module entrypoint handed a workspace
#       node(id: <that workspace>) { ... on Workspace {
#         moduleSource(path: <ref>) { asModule { serve } } } }
#
#   any other target
#       serveModule(address: <ref>, refPin: <pin>)
#
# serveModule resolves a git address itself, and a path in the caller's
# current workspace. That is the right workspace for a plain program, and for
# a module the engine runs as itself. Under a Dang entrypoint it is not: the
# module's code runs in an exec the entrypoint starts, the engine gives that
# exec no module context, and the process is a plain nested client whose
# current workspace is the one the engine finds in its own container. The
# engine gives the entrypoint the module's workspace instead, and the
# entrypoint sends it with the call (see use_entrypoint_workspace), so a path
# resolves there. A git address depends on no workspace, so it keeps
# serveModule either way.
#
# A field that took the workspace, serveModule(address, refPin, workspace),
# would make the two one query again; that is the engine's to add.

# The workspace a module entrypoint handed this process, as its ID, or None.
# One per process: the entrypoint runs one call per process, and every
# session in it talks to the same engine session, which the ID belongs to.
_entrypoint_workspace: str | None = None


def use_entrypoint_workspace(workspace_id: str | None) -> None:
    """Resolve local targets in the workspace a module entrypoint handed over.

    Only ``python -m dagger.mod call`` sets it, from the request the entrypoint
    sends. None, or an empty ID, goes back to serveModule for every target.
    """
    global _entrypoint_workspace  # noqa: PLW0603
    _entrypoint_workspace = workspace_id or None


def entrypoint_workspace() -> str | None:
    """The workspace a module entrypoint handed this process, if any."""
    return _entrypoint_workspace


async def load_target(session: Session, target: Target) -> None:
    """Serve the module a target names."""
    ctx = Context(session)
    if _is_local(target) and _entrypoint_workspace is not None:
        await _serve_in_workspace(ctx, _entrypoint_workspace, target)
        return
    args = [Arg("address", target.ref), Arg("refPin", target.pin, None)]
    try:
        await ctx.root_select("serveModule", args).execute()
    except QueryError as e:
        missing = missing_field(e)
        if missing is None or missing.groups() != ("serveModule", "Query"):
            raise
        await _serve_without_the_field(ctx, target)


def _is_local(target: Target) -> bool:
    # The engine's own rule for a workspace path: an explicit one. A bare name
    # is refused there, and never written into a descriptor.
    return target.ref.startswith((".", "/"))


async def _serve_in_workspace(ctx: Context, workspace_id: str, target: Target) -> None:
    await (
        ctx.select_id("Workspace", workspace_id)
        .select("Workspace", "moduleSource", [Arg("path", target.ref)])
        .select("ModuleSource", "asModule", [])
        .select("Module", "serve", [])
        .execute()
    )


# Only for an engine that predates serveModule, which answers that the field
# does not exist. The field is in dagger/dagger from 284cd849
# (dagger/dagger#14210), so this serves engines released before a release
# carries that commit. This is not a design choice: delete it, with the tests
# that pin its wire shape, once the minimum supported engine has the field.
# Until then it serves what serveModule would, under the module's own name.
# The engine's answer is not remembered, on purpose: on such an engine each
# load pays one failed round trip first, which is cheaper than tracking
# engine versions for a path that goes away.
async def _serve_without_the_field(ctx: Context, target: Target) -> None:
    await (
        _source(ctx, target)
        .select("ModuleSource", "asModule", [])
        .select("Module", "serve", [])
        .execute()
    )


def _source(ctx: Context, target: Target) -> Context:
    if _is_local(target):
        return ctx.root_select("currentWorkspace", []).select(
            "Workspace", "moduleSource", [Arg("path", target.ref)]
        )
    return ctx.root_select(
        "moduleSource",
        [Arg("refString", target.ref), Arg("refPin", target.pin, None)],
    )
