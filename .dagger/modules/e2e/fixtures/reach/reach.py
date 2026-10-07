"""What module code can read of its caller's files: nothing.

Copied into a module that runs on an entrypoint. `reach` takes every ID module
code holds without asking its caller, whatever the SDK's loader keeps and its
own current workspace, and drives each down every route on a `ModuleSource`
(and a `Module`, and a `Workspace`) that could rebuild a directory from
somewhere other than the source's own loaded files: the caller's host, its
`currentWorkspace`, or a workspace the source secretly retained. That last
shape is the one that defeated an earlier fix, where a
`Workspace.moduleSource` result reloaded its context from the workspace on
`withIncludes`. This proves it against the engine rather than by reading the
engine's code.

Each route reads a file that exists only at the caller's workspace root and is
never in the module's files. A route that returns it is a finding. A route that
fails is only reassuring if it failed because the capability was refused, not
because the field does not exist on this engine: the two are told apart, and
the summary reports the split so a run that proved nothing cannot read as a
pass.
"""

from __future__ import annotations

import dataclasses

from dagger._exceptions import QueryError
from dagger.client import _load
from dagger.client._core import Arg, Context

# A leak by climbing is only reachable when the number of "../" lands exactly
# on the caller's workspace root: too few stays inside the module, too many
# escapes the context and the engine refuses the whole pattern. The module's
# depth is not known here, so every route that climbs is tried at each depth.
DEPTHS = range(1, 13)


def _ups() -> list[str]:
    return ["../" * n for n in DEPTHS]


def _held_ids() -> list[str]:
    """Every ID-shaped string the loader holds, however it holds it."""
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


async def _own_workspace_id() -> str:
    return (
        await Context()
        .root_select("currentWorkspace", [])
        .select("Workspace", "id", [])
        .execute(str)
    )


def _read_dir(dctx: Context, secret: str) -> dict[str, Context]:
    """Reach the secret from a Directory: a plain read in case a reload already
    pulled it in at the root, and a climb at each depth, by file path and by
    stepping up with `directory("../")` first.
    """
    routes = {"file": _file(dctx, secret)}
    for up in _ups():
        n = up.count("../")
        routes[f"file(../x{n})"] = _file(dctx, up + secret)
        routes[f"directory(../x{n}).file"] = _file(
            dctx.select("Directory", "directory", [Arg("path", up)]), secret
        )
    return routes


def _file(dctx: Context, path: str) -> Context:
    return dctx.select("Directory", "file", [Arg("path", path)]).select(
        "File", "contents", []
    )


def _source_dirs(source: Context, secret: str) -> dict[str, Context]:
    """Directory-producing routes on a ModuleSource: every field that rebuilds
    or exposes a context, each a chance to reach past the loaded files.

    Rejected, with reason:
      - asString/pin/digest/version/commit/cloneRef/cloneURL/htmlURL/
        repoRootPath/sourceSubpath/originalSubpath: scalars, no directory.
      - localContextDirectoryPath: a host path string, and only for a local
        source. No contents.
      - introspectionSchemaJSON/clientSchemaIntrospectionJSON: schema JSON of
        the module, not a directory of files.
      - generate(workspace:): needs a Workspace argument, which is the very
        capability the module does not have.
      - withDependencies/withBlueprint/withToolchains/withUpdate*: attach other
        modules by ref; they do not rebuild this source's context from the
        caller's workspace, and any ref resolves in the module's own nested
        context. Their result is still walked through `dependencies` below.
      - withClient/withUpdatedClients: mutate the client list, not the context
        root; the result's contextDirectory is still the source's own, which
        the `withName`/`withSDK`/`withEngineVersion` mutations already cover.
    """
    routes: dict[str, Context] = {}

    def add(label: str, dctx: Context):
        for how, ctx in _read_dir(dctx, secret).items():
            routes[f"{label} {how}"] = ctx

    def src_ctx(s: Context) -> Context:
        return s.select("ModuleSource", "contextDirectory", [])

    add("contextDirectory", src_ctx(source))
    # withIncludes an escaping pattern reloads the context from wherever the
    # source came from; at the exact depth this reaches the workspace root.
    # withSourceSubpath and directory climb the same way. Each depth is its own
    # route: the engine refuses a whole pattern list if any pattern escapes.
    for up in _ups():
        n = up.count("../")
        add(
            f"withIncludes(../x{n}).contextDirectory",
            src_ctx(
                source.select(
                    "ModuleSource", "withIncludes", [Arg("patterns", [up + secret])]
                )
            ),
        )
        add(
            f"withSourceSubpath(../x{n}).contextDirectory",
            src_ctx(
                source.select("ModuleSource", "withSourceSubpath", [Arg("path", up)])
            ),
        )
        add(
            f"directory(../x{n})",
            source.select("ModuleSource", "directory", [Arg("path", up)]),
        )
    add(
        "generatedContextDirectory",
        source.select("ModuleSource", "generatedContextDirectory", []),
    )
    add(
        "updatedConfigDirectory",
        source.select("ModuleSource", "updatedConfigDirectory", []),
    )
    add(
        "generatedContextChangeset.layer",
        source.select("ModuleSource", "generatedContextChangeset", []).select(
            "Changeset", "layer", []
        ),
    )
    # Mutations that clone and may reload the context.
    for label, field, arg in (
        ("withName", "withName", Arg("name", "reach")),
        ("withSDK", "withSDK", Arg("source", "")),
        ("withEngineVersion", "withEngineVersion", Arg("version", "v1.0.0-0")),
    ):
        add(
            f"{label}.contextDirectory",
            src_ctx(source.select("ModuleSource", field, [arg])),
        )
    return routes


async def _sub_source_ids(source: Context) -> list[str]:
    """One level down: the source's dependencies, and its own asModule.source,
    each a ModuleSource whose routes are worth the same walk.
    """
    ids: list[str] = []
    try:
        deps = await source.select("ModuleSource", "dependencies", []).execute(
            list[_Ref]
        )
        ids += [d.id for d in deps]
    except Exception:  # noqa: BLE001 - a source may have no dependencies
        pass
    try:
        ids.append(
            await source.select("ModuleSource", "asModule", [])
            .select("Module", "source", [])
            .select("ModuleSource", "id", [])
            .execute(str)
        )
    except Exception:  # noqa: BLE001 - not every ID is a loadable module
        pass
    return ids


@dataclasses.dataclass
class _Ref:
    id: str


def _all_routes(held: str, secret: str) -> dict[str, Context]:
    """Every route from one held ID: as a workspace, as a module source, and as
    a Module whose source is walked the same way.
    """
    routes: dict[str, Context] = {
        "workspace.file": Context()
        .select_id("Workspace", held)
        .select("Workspace", "file", [Arg("path", "/" + secret)])
        .select("File", "contents", []),
        "workspace.directory": _file(
            Context()
            .select_id("Workspace", held)
            .select("Workspace", "directory", [Arg("path", "/")]),
            secret,
        ),
    }
    as_source = Context().select_id("ModuleSource", held)
    for label, ctx in _source_dirs(as_source, secret).items():
        routes[f"source {label}"] = ctx
    mod_source = Context().select_id("Module", held).select("Module", "source", [])
    for label, ctx in _source_dirs(mod_source, secret).items():
        routes[f"module.source {label}"] = ctx
    return routes


def _is_absent(error: QueryError) -> bool:
    """The field does not exist on this engine: a validation error, before any
    resolver runs. Anything else means a resolver ran and refused.
    """
    for e in error.errors:
        if e.path is None and e.extensions.get("code") == "GRAPHQL_VALIDATION_FAILED":
            return True
    return False


async def _probe(ctx: Context) -> tuple[str, str]:
    try:
        value = await ctx.execute(str)
    except QueryError as e:
        return ("absent" if _is_absent(e) else "refused", str(e)[:120])
    except Exception as e:  # noqa: BLE001
        return ("errored", str(e)[:120])
    return ("read", value.strip() if value else "")


async def reach(secret: str) -> str:
    held = [*_held_ids(), await _own_workspace_id()]
    # The held IDs, plus one level of sub-sources reached from each.
    sources = list(held)
    for i in held:
        sources += await _sub_source_ids(Context().select_id("ModuleSource", i))

    reads: list[str] = []
    counts = {"read": 0, "refused": 0, "absent": 0, "errored": 0}
    absent_routes: list[str] = []
    total = 0
    for i in sources:
        for label, ctx in _all_routes(i, secret).items():
            status, detail = await _probe(ctx)
            total += 1
            counts[status] += 1
            # The file exists only at the caller's workspace root, never in the
            # module's loaded files, so any successful read of it is a leak,
            # whatever its contents.
            if status == "read":
                reads.append(f"{label}: {detail}")
            elif status == "absent":
                absent_routes.append(label)

    own = (
        Context()
        .root_select("currentWorkspace", [])
        .select("Workspace", "directory", [Arg("path", "/")])
    )
    status, detail = await _probe(_file(own, secret))
    total += 1
    counts[status] += 1
    if status == "read":
        reads.append(f"currentWorkspace.directory: {detail}")

    absent = ",".join(sorted(set(absent_routes)))
    return (
        f"held={len(held)} sources={len(sources)} routes={total} "
        f"reads={reads} refused={counts['refused']} absent={counts['absent']} "
        f"errored={counts['errored']} absentRoutes=[{absent}]"
    )
