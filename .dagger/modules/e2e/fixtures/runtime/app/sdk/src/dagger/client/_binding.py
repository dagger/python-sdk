"""Serve a generated client's bound module before its first query.

A generated client under ``dagger.clients`` carries a :class:`ModuleBinding`:
the identity of the one module it is bound to. The binding rides on the query
context of every object the client builds, and is served into the session the
first time a query through that client executes.
"""

import dataclasses
import json
import logging
import typing

import gql

if typing.TYPE_CHECKING:
    from dagger.client._session import BaseConnection

logger = logging.getLogger(__name__)

GIT_SOURCE = "GIT_SOURCE"
LOCAL_SOURCE = "LOCAL_SOURCE"

_module_runtime = False


def mark_module_runtime() -> None:
    """Record that this process is a Dagger module runtime.

    The engine builds a module's session with the module's dependencies and the
    module itself already served, before the module runs a single query. A
    module only vendors clients for those, so there is nothing a client could
    serve that is not already there — and a local binding baked into a module
    consumed from git would resolve against the caller's workspace, not the
    module's. Serving is therefore skipped for the whole process.
    """
    global _module_runtime  # noqa: PLW0603
    _module_runtime = True


@dataclasses.dataclass(frozen=True, slots=True)
class ModuleBinding:
    """The identity a generated client serves its module under.

    Parameters
    ----------
    name:
        The module's final name, after any dependency alias. It is the name
        the client chains on ``Query`` and the name the module is served as.
    kind:
        ``LOCAL_SOURCE`` for a module in the workspace, ``GIT_SOURCE`` for a
        remote one.
    ref:
        A workspace-root-relative path with a leading slash for a local
        module; the canonical git ref for a remote one.
    pin:
        The resolved commit of a remote module, empty for a local one.
    """

    name: str
    kind: str
    ref: str
    pin: str = ""

    def __post_init__(self):
        if self.kind not in (LOCAL_SOURCE, GIT_SOURCE):
            msg = f"unsupported module source kind for a client: {self.kind!r}"
            raise ValueError(msg)

    def query(self) -> str:
        """The document that serves the module, as raw GraphQL.

        Raw rather than built through the DSL: ``currentWorkspace`` is hidden
        from a module's codegen schema but present in every live session, and
        the query builder only knows the schema the session fetched on connect.
        """
        serve = f"withName(name: {json.dumps(self.name)}) {{ asModule {{ serve }} }}"
        if self.kind == GIT_SOURCE:
            source = (
                f"moduleSource(refString: {json.dumps(self.ref)}, "
                f"refPin: {json.dumps(self.pin)})"
            )
            return f"{{ {source} {{ {serve} }} }}"
        source = f"currentWorkspace {{ moduleSource(path: {json.dumps(self.ref)})"
        return f"{{ {source} {{ {serve} }} }} }}"

    async def ensure_served(self, conn: "BaseConnection") -> None:
        """Serve the module into the connection's session, once.

        Unconditional on the first use per session — the engine deduplicates a
        repeat of the same source and reports a different source under the same
        name — then remembered on the session so later uses cost nothing. The
        schema the session caches predates the serve, so it is fetched again
        before the binding is marked served.
        """
        if _module_runtime:
            return
        session = conn.session
        async with session.serve_lock:
            if self in session.served:
                return
            logger.debug("Serving module %s from %s", self.name, self.ref)
            await session.execute(gql.gql(self.query()))
            await session.refetch_schema()
            session.served.add(self)
