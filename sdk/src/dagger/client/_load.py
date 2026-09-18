"""How the module a target names gets served into a session."""

from dagger.client._core import Arg, Context
from dagger.client._descriptor import Target
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
    # An engine without the field, below this SDK's floor, fails the load
    # here: a ClientLoadError, not a stale client, since regenerating the
    # client cannot give the engine a field.
    args = [Arg("address", target.ref), Arg("refPin", target.pin, None)]
    await ctx.root_select("serveModule", args).execute()


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
