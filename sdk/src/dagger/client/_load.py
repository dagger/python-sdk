"""How the module a target names gets served into a session."""

from dagger.client._core import Arg, Context
from dagger.client._descriptor import Target
from dagger.client._session import Session

# This is the one place that knows how a module is loaded. The engine field
# this SDK wants, `serveModule(ref, name, pin)`, takes a path or a git ref in
# one argument and resolves a path in the caller's own context. It does not
# exist yet. Until it lands, a git ref goes through the chain the engine has
# today, and a path resolves in the caller's workspace, which a module session
# cannot do. When the field lands, this function alone changes.


async def load_target(session: Session, target: Target) -> None:
    """Serve the module a target names, under the client's name."""
    await (
        _source(Context(session), target)
        .select("ModuleSource", "withName", [Arg("name", target.name)])
        .select("ModuleSource", "asModule", [])
        .select("Module", "serve", [])
        .execute()
    )


def _source(ctx: Context, target: Target) -> Context:
    if _is_local(target.ref):
        return ctx.root_select("currentWorkspace", []).select(
            "Workspace", "moduleSource", [Arg("path", target.ref)]
        )
    return ctx.root_select(
        "moduleSource",
        [Arg("refString", target.ref), Arg("refPin", target.pin, None)],
    )


def _is_local(ref: str) -> bool:
    # A local descriptor is written as a path from the workspace root.
    return ref.startswith((".", "/"))
