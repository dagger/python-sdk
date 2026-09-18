"""How the module a target names gets served into a session."""

from dagger._exceptions import QueryError
from dagger.client._core import Arg, Context
from dagger.client._descriptor import Target, missing_field
from dagger.client._session import Session

# One field, `serveModule(address, refPin)`, serves a git address and a
# workspace path alike: the engine resolves each in the caller's own context,
# which is what lets one client work in a module and in a plain program. The
# descriptor's ref is the address and its pin the refPin. There is no name
# argument: the engine derives the name from the module's own config, as it
# did for the schema this client was generated from, so the two agree unless
# the module renamed itself since, and then the client is stale and its first
# selection says so (see stale_client_error).


async def load_target(session: Session, target: Target) -> None:
    """Serve the module a target names."""
    ctx = Context(session)
    args = [Arg("address", target.ref), Arg("refPin", target.pin, None)]
    try:
        await ctx.root_select("serveModule", args).execute()
    except QueryError as e:
        missing = missing_field(e)
        if missing is None or missing.group(1) != "serveModule":
            raise
        await _serve_without_the_field(ctx, target)


# Only for an engine older than serveModule (dagger/dagger#14210), which
# answers that the field does not exist. This is not a design choice: delete
# it, with the tests that pin its wire shape, once no supported engine
# predates the field.
async def _serve_without_the_field(ctx: Context, target: Target) -> None:
    await (
        _source(ctx, target)
        .select("ModuleSource", "withName", [Arg("name", target.name)])
        .select("ModuleSource", "asModule", [])
        .select("Module", "serve", [])
        .execute()
    )


def _source(ctx: Context, target: Target) -> Context:
    if target.ref.startswith((".", "/")):
        return ctx.root_select("currentWorkspace", []).select(
            "Workspace", "moduleSource", [Arg("path", target.ref)]
        )
    return ctx.root_select(
        "moduleSource",
        [Arg("refString", target.ref), Arg("refPin", target.pin, None)],
    )
