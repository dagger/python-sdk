"""How the module a target names gets served into a session."""

import json
from collections.abc import Mapping
from typing import Any

from dagger._exceptions import ClientLoadError
from dagger.client._core import Arg, Context
from dagger.client._descriptor import GENERATE_HINT, Target
from dagger.client._session import Session

# A descriptor has one shape for a git ref and for a workspace path: `ref` is
# the address, `pin` the refPin. There is no name to pin: the engine derives
# it from the module's own config, as it did for the schema this client was
# generated from, so the two agree unless the module renamed itself since,
# and then the client is stale and its first selection says so (see
# stale_client_error).
#
# Two queries serve a target, and one condition picks between them:
#
#   a local target, in a process a module entrypoint runs
#       node(id: <the source handed over for that client>) {
#         ... on ModuleSource { asModule { serve } } }
#
#   any other target
#       serveModule(address: <ref>, refPin: <pin>)
#
# serveModule resolves a git address itself, and a path in the caller's
# current workspace. That is the right workspace for a plain program. Under
# a Dang entrypoint it is not: the module's code runs in an exec the
# entrypoint starts, the engine gives that exec no module context, and the
# process is a plain nested client whose current workspace is the one the
# engine finds in its own container. The entrypoint resolves the clients the
# caller declared on the module's scope and hands each over by name, as a
# source over that client's own files (entrypoint/handover.dang). The
# workspace never reaches this process: in Dagger an ID is a capability, and
# this is third-party code. A git address depends on no workspace, so it
# keeps serveModule either way.


class _Handover:
    """What a module entrypoint handed this process with its call."""

    def __init__(self, clients: Mapping[str, str] | None):
        # None when the entrypoint sent nothing: one from before the
        # handover, or not one this SDK wrote.
        self.clients = clients

    def source(self, target: Target) -> str:
        if self.clients is None:
            msg = (
                f"The module's entrypoint handed over no clients, so the local "
                f"client {target.name!r} cannot load: the entrypoint is not the "
                f"one this SDK writes, or predates it. {GENERATE_HINT}"
            )
            raise ClientLoadError(msg, target=target)
        if (source := self.clients.get(target.name)) is None:
            msg = (
                f"The local client {target.name!r} is not declared on this "
                f"module's scope in the caller's workspace, so the module's "
                f"entrypoint did not hand it over. Declare it with `dagger "
                f"module client add`. {GENERATE_HINT}"
            )
            raise ClientLoadError(msg, target=target)
        return source


# One per process: the entrypoint runs one call per process.
_handover: _Handover | None = None


def use_entrypoint_clients(clients: Mapping[str, str] | None) -> None:
    """Resolve local targets through what a module entrypoint handed over.

    Only ``python -m dagger.mod call`` sets it, from the request, and always:
    a process that an entrypoint runs never falls back to serveModule for a
    local target, which would resolve it in the process's own container.
    None means the entrypoint sent no clients at all.
    """
    global _handover  # noqa: PLW0603
    _handover = _Handover(None if clients is None else dict(clients))


def leave_entrypoint() -> None:
    """Forget the handover, as in a process no entrypoint runs."""
    global _handover  # noqa: PLW0603
    _handover = None


def parse_handed_clients(value: Any) -> dict[str, str] | None:
    """The clients of a call request: name to module source ID.

    The entrypoint sends a list of ``{"name", "source"}``, as JSON or as its
    encoding; a missing field is None, an entrypoint that sends none.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        msg = f"expected a list of handed clients, got {type(value).__name__}"
        raise TypeError(msg)
    clients: dict[str, str] = {}
    for entry in value:
        name, source = entry["name"], entry["source"]
        if not isinstance(name, str) or not isinstance(source, str):
            msg = f"a handed client needs a string name and source: {entry!r}"
            raise TypeError(msg)
        clients[name] = source
    return clients


async def load_target(session: Session, target: Target) -> None:
    """Serve the module a target names."""
    ctx = Context(session)
    if _is_local(target) and _handover is not None:
        await _serve_handed(ctx, _handover.source(target))
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


async def _serve_handed(ctx: Context, source_id: str) -> None:
    await (
        ctx.select_id("ModuleSource", source_id)
        .select("ModuleSource", "asModule", [])
        .select("Module", "serve", [])
        .execute()
    )
