# Unified clients for the Python SDK

Status: proposed, design only
Date: 2026-09-15
Repo base: `290ed4c`
Spec: "Unified clients" (language-neutral). Prior art: `dagger/java-sdk#23`.

This is the markdown copy of the HTML design page. Badges:
**[confident]** checked against code or by experiment.
**[provisional]** a design that needs sign-off.
**[speculative]** not verified; a phase 1 spike must confirm it.
**[open]** a product or API decision. Each has a recommendation.

## 1. Summary

- A client artifact is a small Python project: a directory with a `pyproject.toml`.
- The SDK generates one artifact per client into `.dagger/clients/python/<name>`.
- Core is an artifact too: `.dagger/clients/python/core`.
- All generated code lives in one namespace package, `dagger_clients`. The
  runtime keeps the name `dagger`. The two names cannot collide.
- The way into a client is a function in its own package: `linter()`, `core()`.
- `dagger.dag` becomes a hand-written `Session`. It is no longer a generated class.
- Each client package carries its descriptor and serves its module on first
  use, once per session.
- The runtime imports generated core in many places today. Phase 1 must remove
  these imports.
- No hard blocker. `contextModuleSource` does not exist yet. It blocks only one
  case: a local client called from a module. An interim path covers that case.

## 2. Terms

The spec terms apply without change: client, scope, client project, consumer,
core, runtime, serve. Python terms:

| Term | Meaning here |
| --- | --- |
| Distribution | What a package manager installs, for example `dagger-clients-linter`. |
| Import package | What Python code imports, for example `dagger_clients.linter`. |
| Namespace package | An import package without `__init__.py` (PEP 420). Many distributions can each add one sub-package to it. |
| Client artifact | The generated directory for one client: one distribution with one import package. |
| Client package | The import package inside a client artifact: `dagger_clients.<name>`. |
| Descriptor | The data that tells a client package where its module is: a workspace path, or a git ref and pin. |

## 3. What exists today [confident]

The current SDK generates per scope. Each module gets its own copy of everything.

- `mod.dang:138` (`vendoredDir`) copies the whole `dagger-io` library into `<module>/sdk/`.
- `mod.dang:192` (`bindings`) generates one file, `sdk/src/dagger/client/gen.py`.
  It holds core and every client of the module. The input is the module-facing
  schema, `introspectionSchemaJSON`.
- The generator emits `class Client(Query)` and `dag = Client()`
  (`sdk/codegen/src/codegen/generator.py:245-257`).
- Module code reaches a client through core: `dag.client_dep()`.
- `python-sdk.dang:145-147` writes each client as `[[dependencies]]`. The engine
  serves the client. Generated code serves nothing.
- `python-sdk.dang:100-105` refuses clients in a scope without a module.
- `findClientRoot` exists (`python-sdk.dang:42`), with `pyproject.toml` as the marker.
- The runtime build installs `./sdk` (`runtime/build.dang:167-189`).
- The committed core bindings (engine `v1.0.0-beta.10`) have
  `ModuleSource.clientSchemaIntrospectionJSON`, `ModuleSource.withName`,
  `Query.moduleSource(refString, refPin)`, `Module.serve(includeDependencies, entrypoint)`,
  `SourceMap.module`. `Query.contextModuleSource` does not exist.
- The generator already parses schema directives (`sdk/codegen/src/codegen/ast.py:93`).

## 4. Target layout [provisional]

```mermaid
graph BT
  RT["dagger (dagger-io): runtime, hand-written, no engine version"]
  CORE["dagger_clients.core: generated, pinned to one core schema"]
  L["dagger_clients.linter + descriptor"]
  F["dagger_clients.format + descriptor"]
  G["dagger_clients.glow + descriptor"]
  CORE --> RT
  L --> CORE
  F --> CORE
  G --> CORE
  L --> RT
  F --> RT
  G --> RT
```

```mermaid
graph LR
  subgraph WS["workspace"]
    MOD["my-project-dev (module)"]
    TP["test-project (plain program)"]
    subgraph OUT[".dagger/clients/python"]
      CORE["core"]
      L["linter"]
      F["format"]
      G["glow"]
    end
    LM[".dagger/modules/linter"]
    FM[".dagger/modules/format"]
  end
  GLOW["github.com/eunomie/glow"]
  MOD --> L
  MOD --> F
  MOD --> G
  TP --> L
  TP --> G
  L -. serves .-> LM
  F -. serves .-> FM
  G -. serves .-> GLOW
```

```
.dagger/clients/python/
  core/
    pyproject.toml            name = "dagger-clients-core"
    src/dagger_clients/       no __init__.py: a namespace package
      core/__init__.py        generated types, core(), CORE_DIGEST
      core/py.typed
  linter/
    pyproject.toml            name = "dagger-clients-linter"
                              dependencies = ["dagger-io", "dagger-clients-core"]
                              [tool.uv.sources] dagger-clients-core = { path = "../core" }
    src/dagger_clients/
      linter/__init__.py      generated types, linter(), as_linter()
      linter/_target.py       descriptor and the core digest it was generated against
      linter/py.typed
```

## 5. Packaging

**What a client artifact is [confident].** A directory with `pyproject.toml`
and `src/`, built with `uv_build`. It builds into a wheel without an engine.
Tested with uv 0.12.13:

- `module-name = "dagger_clients.linter"` builds a wheel with only `dagger_clients/linter/`.
- Two editable path installs share the `dagger_clients` namespace.
- A consumer that names only `dagger-clients-linter` also gets core: uv follows
  the path source in the client's own `pyproject.toml`.
- With `py.typed` per sub-package, mypy and pyright report type errors across
  the namespace.

**How a consumer declares a dependency [provisional].**

```toml
# test-project/pyproject.toml
[project]
dependencies = ["dagger-io", "dagger-clients-linter", "dagger-clients-glow"]

[tool.uv.sources]
dagger-clients-linter = { path = "../.dagger/clients/python/linter", editable = true }
dagger-clients-glow   = { path = "../.dagger/clients/python/glow",   editable = true }
```

pip ignores `[tool.uv.sources]`, so a pip user installs core explicitly too.

**Where the build gets it.**

- Plain program: the package manager reads the path.
- Module: the runtime build sees only the module context directory.
  `generateScope` adds the clients tree to `include`. [speculative] An include
  above the module root is not verified (Q8).
- Runtime: this repo does not publish `dagger-io` (Q2).

## 6. Namespacing [confident mechanism, open name]

Generated code goes into the top-level namespace package `dagger_clients`. A
client `telemetry` becomes `dagger_clients.telemetry` and cannot collide with
`dagger.telemetry`. Core is `dagger_clients.core`, so a client named `core` is
refused.

`dagger.clients.<name>` is not possible: `dagger` is a regular package, and
type checkers do not follow `pkgutil.extend_path` across installs.

| Input | Rule | Example |
| --- | --- | --- |
| Client name | Lowercase. Replace `-` and `.` with `_`. | `my-project-dev` → `my_project_dev` |
| Refused | Not an identifier, a keyword, starts with `_`, `core`, two clients with the same result. | `class`, `core` |
| Distribution | `dagger-clients-` + name with `-` | `dagger-clients-my-project-dev` |
| Root class | Today's codegen rule | `MyProjectDev` |
| Entry function | Snake-case name | `my_project_dev()` |

## 7. Entry point [provisional]

```python
# A module (consumer)
from dagger import function, object_type
from dagger_clients.core import Directory
from dagger_clients.linter import linter

@object_type
class MyProjectDev:
    @function
    async def lint(self, src: Directory) -> str:
        return await linter().lint(src)
```

```python
# A plain program (consumer)
import anyio
import dagger
from dagger_clients.core import core
from dagger_clients.linter import linter

async def main():
    async with dagger.connection():
        src = core().host().directory(".")
        print(await linter().lint(src))

anyio.run(main)
```

Constructor arguments follow today's rules: required are positional, optional
are keyword-only. The session is an optional keyword-only argument; without it
the function uses `dagger.dag`. A GraphQL argument named `session` becomes
`session_` (Q3).

```python
def linter(source: Directory, *, config: str | None = None,
           session: Session | None = None) -> Linter: ...
```

A field a client contributes to a core type becomes a module-level function
with the core receiver first. The receiver holds its session (Q4).

```python
from dagger_clients.linter import as_linter

lint = as_linter(binding)          # was: binding.as_linter()
```

Two core types that contribute a field with one name get one function with
`@typing.overload` per receiver type.

## 8. Session handle and serve

**`dagger.dag` [confident on shape].** It becomes an instance of hand-written
`dagger.Session`. The session owns the connection, the query transport and the
serve memo. It has no API fields.

- `dagger.connection()` and `dagger.Connection` yield a `Session`.
- `Session.__getattr__` raises `AttributeError` with a migration message that
  names `core().container()`. The runtime does not import core for this.
- A PEP 562 `__getattr__` on `dagger` does the same for `dagger.Container` and
  other core names.

**Serve [provisional].** Serve is async and `linter()` is sync, so serve runs at
execute time.

1. The runtime `Context` gets the set of descriptors the query needs.
2. `linter()` creates a `Context` that needs the linter descriptor.
3. Chained selections keep the set.
4. `Context.execute` asks the session to serve each descriptor first.
5. The session serves each descriptor at most once, with one `anyio.Lock` per
   entry. Two sessions each serve.
6. An object passed as an argument becomes an ID through its own `execute`,
   which serves its own descriptor.
7. The module runtime loads arguments from IDs in `dagger/mod/_converter.py:62`.
   The generated class carries its descriptor for this.

```python
# dagger_clients/linter/_target.py (generated)
from dagger.client import Target

TARGET = Target.local(name="linter", path="/.dagger/modules/linter")
# or: Target.git(name="glow", ref="github.com/eunomie/glow", pin="4f1c9e…")
CORE_DIGEST = "sha256:…"
```

| Descriptor | Client session | Module session |
| --- | --- | --- |
| git | `moduleSource(refString, refPin)` | `moduleSource(refString, refPin)` |
| local, today | `currentWorkspace.moduleSource(path)` | No serve; `[[dependencies]]` (interim, Q7) |
| local, after the engine change | `contextModuleSource(path)` | `contextModuleSource(path)` |

## 9. Type checking and staleness [provisional]

| When | Signal | Needs the engine |
| --- | --- | --- |
| Type check | `py.typed` and full annotations. A removed or changed function is a type error after `dagger generate`. | No |
| Import | The client compares its `CORE_DIGEST` with `dagger_clients.core.CORE_DIGEST`. A mismatch raises `dagger.StaleClientError` (an `ImportError`) with "run `dagger generate`". The check is in generated code. | No |
| Serve and query | A failed serve raises `dagger.ClientServeError`. A "cannot query field" error on a query that needs a descriptor becomes `StaleClientError`. | Yes |

A CI check that runs `dagger generate` and asserts no diff catches the rest (Q11).

## 10. Artifact graph rules in Python

| Rule | Python meaning | Check |
| --- | --- | --- |
| Core names no client | `dagger_clients/core/` imports no other `dagger_clients` package. | AST scan; generate core with two client sets and compare digests. |
| No client names another | A client imports only `dagger`, `dagger_clients.core`, itself. | AST scan. |
| Runtime depends on nothing generated | No `dagger/` module imports `dagger_clients`, `dagger.client.gen`, `dagger_gen`, directly or through `dagger/__init__.py`. | Import every runtime module without `dagger_clients`; AST scan. |

Where the runtime depends on generated core today [confident]. `dagger/telemetry.py`
is clean; the trap is elsewhere:

| Location | Dependency | Proposed fix |
| --- | --- | --- |
| `sdk/src/dagger/__init__.py:10-16` | Star-imports `dagger_gen` or `dagger.client.gen`. | Remove; add PEP 562 message. |
| `sdk/src/dagger/client/_core.py:24`, `client/_session.py:11` | `from dagger import …` runs `dagger/__init__.py`, which loads core. The Python-specific trap. | Import from `dagger._exceptions`, `dagger.telemetry`. |
| `sdk/src/dagger/mod/_module.py:19-20, 1054-1137`, `_converter.py:7-8, 165-180`, `_exceptions.py:14-15, 143-147`, `_describe.py:16, 32`, `_entrypoint.py:11, 34` | Type registration through generated `dag`, `TypeDef`, `TypeDefKind`, `FunctionCachePolicy`, `JSON`. | Raw query builder (Q6). |
| `sdk/src/dagger/provisioning/_connection.py:82-83, 97-98`, `_engine.py:12, 31` | Imports `dag`, `Client`. | Return a `Session`. |
| `sdk/src/dagger/_engine/_version.py:3` | Generated `CLI_VERSION` in the runtime, used to download a CLI. | Keep in runtime; revisit in phase 2. |
| `sdk/codegen/src/codegen/generator.py:245-257` | Emits `Client`, `dag`. | Emit `core()`. |
| `sdk/src/dagger/client/_guards.py:44` | Text names `dagger.client.gen`. | Take the name from the caller. |

## 11. generateScope and findClientRoot

**`generateScope` (`python-sdk.dang:99`) [provisional].**

1. For each client: read `clientSchemaIntrospectionJSON`; partition by
   `@sourceMap`; write `.dagger/clients/python/<name>` at the workspace root.
2. Write the descriptor from the `ModuleSource`: `kind`, and the workspace path
   or `asString` and `pin`.
3. Generate core once from the client-facing schema into
   `.dagger/clients/python/core`. [speculative] How to get core alone.
4. Module scope: add each client to `pyproject.toml` dependencies and
   `[tool.uv.sources]`; add the clients tree to `include`; refresh `uv.lock`;
   write `[[dependencies]]` only for local clients (Q7).
5. Client project scope: remove the refusal at `python-sdk.dang:100-105`; write
   the artifacts; do not edit a user's `pyproject.toml` (Q9).

Two scopes that use one client write identical bytes. [speculative] The engine
merges them. A git client no longer needs `[[dependencies]]`, so the static
entrypoint refusal at `python-sdk.dang:154-156` can go for git clients.

**`findClientRoot` (`python-sdk.dang:42`) [provisional].** The marker stays
`pyproject.toml`. A `pyproject.toml` inside `.dagger/clients/python/` is never a
client root; the search continues above it. The `sdk/` lift stays for modules
that still vendor.

## 12. Engine dependency (property 4)

`contextModuleSource` is not in the committed bindings. The `beta.13` schema is
not checked. Tracked in `dagger/dagger#14148`.

| Case | Works without the engine change |
| --- | --- |
| Git client from a plain program | Yes |
| Git client from a module | Yes on Java's engine; not tested here |
| Local client from a plain program | Yes, `currentWorkspace.moduleSource(path)` from a client session |
| Local client from a module | No; interim `[[dependencies]]` |

Not a blocker. Only the last row waits for the engine.

## 13. Open questions

- **Q1. Namespace name.** `dagger_clients`, `dagger_modules`, other.
  Recommendation: `dagger_clients`.
- **Q2. Runtime source.** (a) per-module runtime copy; (b) one workspace copy at
  `.dagger/clients/python/_runtime`; (c) publish now. Recommendation: (b) in
  phase 1, publish in phase 2. The PyPI name is Yves's call.
- **Q3. Session argument.** (a) keyword-only `session=`; (b) first positional;
  (c) `linter.using(session)`. Recommendation: (a).
- **Q4. Contributed field shape.** (a) `as_linter(binding)`; (b)
  `Linter.as_linter(binding)`. Recommendation: (a).
- **Q5. Migration.** Recommendation: opt-in SDK setting in phase 1, as with
  `staticEntrypoint`; migration messages; no forwarding shim; default flip in
  phase 2.
- **Q6. `dagger.mod` and core.** (a) raw query builder; (b) a layer above core.
  Recommendation: (a).
- **Q7. Local clients in modules before `contextModuleSource`.** (a) keep
  `[[dependencies]]`; (b) refuse; (c) `currentWorkspace` from module code.
  Recommendation: (a). (c) builds on the #14148 hole.
- **Q8. Module build sees clients.** (a) `include`; (b) byte-identical copy.
  Recommendation: (a) if a spike confirms it, else (b).
- **Q9. Wiring a plain program.** (a) user edits `pyproject.toml`; (b)
  `generateScope` edits it. Recommendation: (a) in phase 1.
- **Q10. Removing a client.** Recommendation: no deletion in phase 1.
- **Q11. Version check strictness.** Recommendation: client/core mismatch is an
  import error; engine/core mismatch is a warning in phase 1.

## 14. Checks

Invert each assertion once and confirm that it fails.

1. The digest of a client artifact is the same from a module scope and a client project scope.
2. Every `dagger.*` module imports in a venv without `dagger_clients`.
3. AST scan of imports in core and in each client package.
4. Module source that calls a client passes mypy and pyright, then `dagger call` runs it.
5. `uv build --wheel` for each artifact with no engine; the wheel exists and contains the package; it imports in a fresh venv.
6. A changed `CORE_DIGEST` makes a client import raise `StaleClientError`.
7. Fake engine: two calls in one session send one serve; two sessions send two.
8. A module with a git client and a local client loads both through the CLI.

## 15. Phases

**Phase 1: self-serving clients, opt-in.** Remove runtime imports of generated
core. Add `Session`, `Target`, serve memo, error types. Partition in the
generator; emit `dagger_clients.core` and one package per client.
`generateScope` writes `.dagger/clients/python/` for both scope kinds behind a
setting. Git clients serve everywhere; local clients serve from plain programs;
local clients in modules use `[[dependencies]]`. Spikes first: Q8, core alone,
identical writes. Checks 1–8.

**Phase 2: one artifact everywhere.** After `contextModuleSource`: local clients
self-serve in modules; remove `[[dependencies]]`; allow clients with the static
entrypoint. Publish the runtime. Make the layout the default; decide migration.

## 16. Not verified

- [speculative] How `generateScope` gets core alone. Fallback: strip module-owned
  types from a client schema, with Java's skew guard.
- [speculative] `beta.13` introspection carries `@sourceMap(module:)`.
- [speculative] A module `include` can name `../../clients/python/**`.
- [speculative] Two scopes that write identical files merge without conflict.
- [speculative] `Module.serve` from a module session on this repo's engine.
- [speculative] Spec decision 4: a client function whose signature names a type
  from another client.

Verified by experiment with uv 0.12.13: namespace build, shared namespace across
editable path installs, transitive uv path source, mypy and pyright errors
across the namespace.
