# Unified clients for the Python SDK

Status: proposed, design only (revision 3)
Date: 2026-09-15
Repo base: `290ed4c`
Spec: "Unified clients" (language-neutral). Prior art: `dagger/java-sdk#23`.

This is the markdown copy of the HTML design page. Badges:
**[confident]** checked against code or by experiment.
**[decided]** Yves decided it.
**[provisional]** a design that needs sign-off.
**[speculative]** not verified; a phase 1 spike must confirm it.
**[open]** a product or API decision. Each has a recommendation.
**[closed]** a later revision answers it.

Revision 3:

- A scope is one `pyproject.toml`: a uv workspace root. Each client is a
  workspace member inside the scope. A consumer installs only the clients it
  names (5.2).
- This removes the `../core` path, the clients directory setting, and writes
  from two scopes into one directory.
- Closed: Q10. Changed: Q2, Q8, Q9, Q12.

Revision 2:

- The location is a convention. `.dagger/clients/python` is a suggestion (5.4).
- A temporary global client, behind a `pyproject.toml` flag (9, Q5).

## 1. Summary

- A scope is one `pyproject.toml`. It is a uv workspace root.
- Each client is a workspace member inside the scope, with its own small
  generated `pyproject.toml`. Core is a member too.
- A consumer names one client. uv installs that client, core and the runtime.
  It does not install the other clients.
- A member carries no path. Its files are the same in every scope.
- The scope can be anywhere. `.dagger/clients/python` is a suggestion for a
  scope that only holds clients. A module scope is the module directory.
- All generated code lives in one namespace package, `dagger_clients`. The
  runtime keeps the name `dagger`. The two names cannot collide.
- The way into a client is a function in its own package: `linter()`, `core()`.
- `dagger.dag` becomes a hand-written `Session`. It is no longer a generated class.
- Each client package carries its descriptor and serves its module on first
  use, once per session.
- A temporary global client keeps existing module code working. A flag in the
  module's `pyproject.toml` turns it on. A new module has no flag.
- The runtime imports generated core in many places today. Phase 1 must remove
  these imports.
- No hard blocker. `contextModuleSource` does not exist yet. It blocks only one
  case: a local client called from a module. An interim path covers that case.

## 2. Terms

The spec terms apply without change: client, scope, consumer, core, runtime,
serve. Added terms:

| Term | Meaning here |
| --- | --- |
| Scope `pyproject.toml` | The file that defines a scope for this SDK. It is a uv workspace root. It lists the members and their sources. |
| Member | A uv workspace member: a directory with its own `pyproject.toml` inside the scope. Each client, core, and the runtime copy are members. |
| Client project | A scope without a module. Its `pyproject.toml` can hold only the workspace, or it can also be a program that uses its own members. |
| Distribution | What a package manager installs, for example `dagger-clients-linter`. |
| Import package | What Python code imports, for example `dagger_clients.linter`. |
| Namespace package | An import package without `__init__.py` (PEP 420). Many distributions can each add one sub-package to it. |
| Client package | The import package inside a client member: `dagger_clients.<name>`. |
| Descriptor | The data that tells a client package where its module is: a workspace path, or a git ref and pin. |
| Global client | A temporary generated object, `dag`, with one method per core field and per client. It exists only for migration. |

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
- A module's `pyproject.toml` names the vendored runtime:
  `dagger-io = { path = "sdk", editable = true }`
  (`templates/default/pyproject.toml.tmpl`). The runtime build installs it
  (`runtime/build.dang:167-189`).
- Python SDK settings live in `[tool.dagger]` of the module's `pyproject.toml`:
  `use-uv`, `base-image`. `helpers/pyproject/pyproject.go:60-87` reads and
  writes them. `runtime/build.dang:304-313` reads them.
- `sdk/src/dagger/__init__.py:12-13` already has a hook for extra generated
  bindings: `from dagger_gen import *`.
- The committed core bindings (engine `v1.0.0-beta.10`) have
  `ModuleSource.clientSchemaIntrospectionJSON`, `ModuleSource.withName`,
  `Query.moduleSource(refString, refPin)`, `Module.serve(includeDependencies, entrypoint)`,
  `SourceMap.module`. `Query.contextModuleSource` does not exist.
- The generator already parses schema directives (`sdk/codegen/src/codegen/ast.py:93`).

## 4. Target layout [provisional]

An arrow means "imports". The runtime imports no generated code. Section 9 adds
one optional, temporary exception.

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

The worked example. One client project holds the clients. Two consumers each
name the clients they use. Nothing is generated twice.

```mermaid
graph LR
  subgraph WS["workspace"]
    MOD["my-project-dev (module)"]
    TP["test-project (plain program)"]
    subgraph SC["client project scope, for example .dagger/clients/python"]
      ROOT["pyproject.toml (uv workspace root)"]
      CORE["clients/core"]
      L["clients/linter"]
      F["clients/format"]
      G["clients/glow"]
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

Two kinds of scope on disk:

```
.dagger/clients/python/               a client project (suggested location)
  pyproject.toml                      uv workspace root; no [project] table
  clients/_runtime/                   member: dagger-io
  clients/core/                       member: dagger-clients-core
  clients/format/                     member: dagger-clients-format
  clients/glow/                       member: dagger-clients-glow
  clients/linter/                     member: dagger-clients-linter

.dagger/modules/my-module/            a module scope that declares its own clients
  pyproject.toml                      the module project AND the uv workspace root
  dagger-module.toml
  src/my_module/
  clients/_runtime/
  clients/core/
  clients/linter/
  clients/_global/                    only with global-client = true (section 9)
```

## 5. Packaging and scopes

### 5.1 What a client member is [confident]

A directory with `pyproject.toml` and `src/`, built with `uv_build`. It builds
into a wheel without an engine.

```toml
# clients/linter/pyproject.toml (generated)
[project]
name = "dagger-clients-linter"
version = "0.0.0"
dependencies = ["dagger-io", "dagger-clients-core"]

[build-system]
requires = ["uv_build>=0.8.4,<0.12.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "dagger_clients.linter"

[tool.dagger]
generated-client = "linter"
```

```
clients/linter/src/dagger_clients/    no __init__.py: a namespace package
  linter/__init__.py                  generated types, linter(), as_linter()
  linter/_target.py                   descriptor and the core digest it was generated against
  linter/py.typed
```

The member has no `[tool.uv.sources]` and no path. Its files do not depend on
the scope that holds it. One member generated in two scopes had equal digests.

### 5.2 The scope `pyproject.toml` [decided]

The scope `pyproject.toml` is a uv workspace root. It lists the members and
gives each one a workspace source. The SDK owns these entries. The user owns
the rest of the file.

```toml
# .dagger/clients/python/pyproject.toml (a client project)
[tool.uv.workspace]
members = ["clients/_runtime", "clients/core", "clients/format", "clients/glow", "clients/linter"]

[tool.uv.sources]
dagger-io             = { workspace = true }
dagger-clients-core   = { workspace = true }
dagger-clients-format = { workspace = true }
dagger-clients-glow   = { workspace = true }
dagger-clients-linter = { workspace = true }
```

```toml
# .dagger/modules/my-module/pyproject.toml (a module scope)
[project]
name = "my-module"
dependencies = ["dagger-io", "dagger-clients-linter"]

[tool.uv.workspace]
members = ["clients/_runtime", "clients/core", "clients/linter"]

[tool.uv.sources]
dagger-io             = { workspace = true }
dagger-clients-core   = { workspace = true }
dagger-clients-linter = { workspace = true }
```

Tested with uv 0.12.13 [confident]:

- A scope `pyproject.toml` without a `[project]` table works.
- A scope `pyproject.toml` with a `[project]` table can use its own members.
  With sources for three members and a dependency on one, uv installs only that
  member and core.
- `uv sync --locked --no-dev` works on that layout. The runtime build uses that form.
- Removing a member, its source and its directory, then `uv lock`, removes the
  client cleanly.
- Two editable members share the `dagger_clients` namespace. mypy and pyright
  see types across it when each member has `py.typed`.

### 5.3 How a consumer names a client [confident]

Inside the scope: the source already exists. The consumer adds the client to
`[project] dependencies`.

Outside the scope: the consumer names the member by path. uv finds the member's
workspace and takes core and the runtime from it.

```toml
# test-project/pyproject.toml
[project]
dependencies = ["dagger-clients-linter", "dagger-clients-glow"]

[tool.uv.sources]
dagger-clients-linter = { path = "../.dagger/clients/python/clients/linter", editable = true }
dagger-clients-glow   = { path = "../.dagger/clients/python/clients/glow",   editable = true }
```

One scope per environment: a consumer that names members of two scopes gets two
`dagger-clients-core` sources. uv refuses: "Requirements contain conflicting
URLs for package `dagger-clients-core`". The failure is loud.

pip ignores uv sources and workspaces. A pip user installs the members by path,
core and the runtime included. The `CORE_DIGEST` import check is the guard for pip.

### 5.4 Where scopes go [decided: convention only]

- A scope can be anywhere. `.dagger/clients/python` is a suggestion for a client
  project. Nothing requires it.
- A module scope is the module directory. Its clients travel with the module,
  also in a git repository.
- Nothing at run time depends on the location. The descriptor names the module,
  not the member.
- Moving a whole scope changes only the path a consumer outside the scope names.
- Moving core inside the scope changes only the `members` list. Core cannot
  leave the scope: it must be a member of the same workspace as the clients.
- `generateScope` rewrites the SDK-owned entries on each run.

### 5.5 Where the build gets it

- Plain program: uv reads the scope.
- Module that declares its own clients: the members are inside the module
  directory, so they are in the module's context directory. [speculative] The
  runtime build (`runtime/build.dang:167-189`) must accept a workspace module,
  in uv and pip modes.
- Module that uses a client project outside its directory: `include` must add
  the client project (Q8).
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
| Member directory | `clients/` + name with `-` | `clients/my-project-dev` |
| Root class | Today's codegen rule | `MyProjectDev` |
| Entry function | Snake-case name | `my_project_dev()` |

Names that start with `_` are refused for clients, so `clients/_runtime` and
`clients/_global` cannot collide with a client.

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

**`dagger.dag` [confident on shape].** Without the global client, it is an
instance of hand-written `dagger.Session`. The session owns the connection, the
query transport and the serve memo. It has no API fields.

- `dagger.connection()` and `dagger.Connection` yield a `Session`.
- Without the global client, `Session.__getattr__` raises `AttributeError` with
  a migration message that names `core().container()` and the `global-client`
  flag. The runtime does not import core for this.
- Without the global client, a PEP 562 `__getattr__` on `dagger` does the same
  for `dagger.Container` and other core names.

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

The `path` is the workspace path of the module. It does not depend on the scope
that holds the member.

| Descriptor | Client session | Module session |
| --- | --- | --- |
| git | `moduleSource(refString, refPin)` | `moduleSource(refString, refPin)` |
| local, today | `currentWorkspace.moduleSource(path)` | No serve; `[[dependencies]]` (interim, Q7) |
| local, after the engine change | `contextModuleSource(path)` | `contextModuleSource(path)` |

## 9. Temporary global client [decided goal, provisional shape]

### The goal

Existing module code must keep working without a change to the user's code.
That code uses `dag.linter().lint()`, `dag.container()` and `dagger.Container`.
A global client gives it these names during migration. A newly initialized
module does not get the global client.

### The flag

```toml
# <module>/pyproject.toml
[tool.dagger]
global-client = true
```

- The flag is in the same table as `use-uv` and `base-image`.
- No flag means no global client.
- Existing module: `generateScope` writes `global-client = true` once, when it
  upgrades a module to the new layout. It detects an existing module: the
  module had a config before generation (`python-sdk.dang:108`) and has legacy
  bindings at `sdk/src/dagger/client/gen.py`.
- New module: the templates do not contain the flag.
- After migration: `dagger call python-sdk mod config set --global-client=false`
  removes the flag. The next `dagger generate` removes the global client.
  `config get` reports the flag.
- Only generation reads the flag. The runtime does not read `pyproject.toml`.

### What the SDK generates

With the flag, `generateScope` writes one more member in the module scope:
`clients/_global`, distribution `dagger-global-client`, import package
`dagger_global`. The module depends on it. It is the only generated code that
belongs to one consumer, because it lists that module's own clients.

```python
# dagger_global/__init__.py (generated, temporary)
from dagger import Session
from dagger_clients.core import *                  # dagger.Container keeps working
from dagger_clients.core import core
from dagger_clients.linter import Linter, linter   # and every type of each client

class Client(Session):
    def container(self, *, platform=None) -> Container:
        return core(session=self).container(platform=platform)

    def linter(self, source: Directory) -> Linter:
        return linter(source, session=self)

dag = Client()
```

- Same classes: `dag.container()` returns `dagger_clients.core.Container`. Old
  and new calls can mix in one module.
- Contributed fields: old code calls `binding.as_linter()`. The global client
  adds it to the core class at import, at run time only. Type checkers do not
  see it (Q5).
- Plain programs: not covered. This SDK generates no Python client for a plain
  program today (`python-sdk.dang:100-105`).

### How `dagger` finds it

The runtime keeps one optional import. It replaces today's `dagger_gen` hook
(`sdk/src/dagger/__init__.py:12-13`).

```python
# dagger/__init__.py (hand-written)
try:
    from dagger_global import *     # temporary global client, only with the flag
except ModuleNotFoundError:
    from dagger.client._session import dag
```

```mermaid
graph BT
  RT["dagger (runtime)"]
  CORE["dagger_clients.core"]
  L["dagger_clients.linter"]
  GL["dagger_global: temporary, in one module, only with global-client = true"]
  OLD["existing module code: dag.linter(), dag.container()"]
  CORE --> RT
  L --> CORE
  GL --> CORE
  GL --> L
  GL --> RT
  OLD --> RT
  RT -. optional import .-> GL
```

- This is the only place the runtime names generated code. It is a known
  exception to rule 3. It is off for new modules. It goes away with the global
  client.
- The import is static, so mypy and pyright can type `dag` as `Client` when
  `dagger_global` exists. [speculative] Check 9 must confirm it.

## 10. Type checking and staleness [provisional]

| When | Signal | Needs the engine |
| --- | --- | --- |
| Type check | `py.typed` and full annotations. A removed or changed function is a type error after `dagger generate`. | No |
| Install (uv) | Members from two scopes in one environment fail with a conflict on `dagger-clients-core`. Verified by experiment. | No |
| Import | The client compares its `CORE_DIGEST` with `dagger_clients.core.CORE_DIGEST`. A mismatch raises `dagger.StaleClientError` (an `ImportError`) with "run `dagger generate`". The check is in generated code. | No |
| Serve and query | A failed serve raises `dagger.ClientServeError`. A "cannot query field" error on a query that needs a descriptor becomes `StaleClientError`. | Yes |

A CI check that runs `dagger generate` and asserts no diff catches the rest (Q11).

## 11. Artifact graph rules in Python

| Rule | Python meaning | Check |
| --- | --- | --- |
| Core names no client | `dagger_clients/core/` imports no other `dagger_clients` package. | AST scan; generate core with two client sets and compare digests. |
| No client names another | A client imports only `dagger`, `dagger_clients.core`, itself. The global client is not a client package. | AST scan. |
| Runtime depends on nothing generated | No `dagger/` module imports `dagger_clients`, `dagger.client.gen`, `dagger_gen`. One exception: the optional `dagger_global` import. | Import every runtime module without `dagger_clients` and `dagger_global`; AST scan with the one exception. |

Where the runtime depends on generated core today [confident]. `dagger/telemetry.py`
is clean; the trap is elsewhere:

| Location | Dependency | Proposed fix |
| --- | --- | --- |
| `sdk/src/dagger/__init__.py:10-16` | Star-imports `dagger_gen` or `dagger.client.gen`. | Replace with the optional `dagger_global` import; add PEP 562 message. |
| `sdk/src/dagger/client/_core.py:24`, `client/_session.py:11` | `from dagger import …` runs `dagger/__init__.py`, which loads core. The Python-specific trap. | Import from `dagger._exceptions`, `dagger.telemetry`. |
| `sdk/src/dagger/mod/_module.py:19-20, 1054-1137`, `_converter.py:7-8, 165-180`, `_exceptions.py:14-15, 143-147`, `_describe.py:16, 32`, `_entrypoint.py:11, 34` | Type registration through generated `dag`, `TypeDef`, `TypeDefKind`, `FunctionCachePolicy`, `JSON`. | Raw query builder (Q6). |
| `sdk/src/dagger/provisioning/_connection.py:82-83, 97-98`, `_engine.py:12, 31` | Imports `dag`, `Client`. | Return a `Session`. |
| `sdk/src/dagger/_engine/_version.py:3` | Generated `CLI_VERSION` in the runtime, used to download a CLI. | Keep in runtime; revisit in phase 2. |
| `sdk/codegen/src/codegen/generator.py:245-257` | Emits `Client`, `dag`. | Emit `core()`; emit `Client` and `dag` only into `dagger_global`. |
| `sdk/src/dagger/client/_guards.py:44` | Text names `dagger.client.gen`. | Take the name from the caller. |

## 12. generateScope and findClientRoot

**`generateScope` (`python-sdk.dang:99`) [provisional].** The scope directory
(`ws.cwd`) is the workspace root. Both scope kinds share one path.

1. Scope file: read the scope `pyproject.toml`. A scope without one gets a new
   file with only `[tool.uv.workspace]` and `[tool.uv.sources]`.
2. Core: generate once from the client-facing schema into `clients/core`.
   [speculative] How to get core alone.
3. Runtime: write `clients/_runtime` (Q2).
4. Clients: for each client, read `clientSchemaIntrospectionJSON`, partition by
   `@sourceMap`, write the descriptor (`kind`, and the workspace path or
   `asString` and `pin`), write `clients/<name>`.
5. Removed clients: delete each directory under `clients/` that has
   `[tool.dagger] generated-client` and whose client is no longer declared.
6. SDK-owned entries: set the `members` entries under `clients/` and one
   `{ workspace = true }` source per member. Keep all other content, including
   members the user added. [provisional] This needs a TOML editor that keeps
   formatting; `helpers/pyproject` reformats the file today.
7. Module scope: add each client, and `dagger-io`, to `[project] dependencies`
   (Q9). Replace the old `dagger-io = { path = "sdk" }` source. Handle the
   global client flag: write it for an existing module without it; with the
   flag, write `clients/_global` and depend on it; without it, remove both.
   Write `[[dependencies]]` only for local clients (Q7).
8. Scope without a module: remove the refusal at `python-sdk.dang:100-105`. Do
   not touch `[project] dependencies`.
9. Lock: refresh `uv.lock` if it exists.

Each scope writes only inside its own directory. Two scopes never write the
same file. A git client no longer needs `[[dependencies]]`, so the static
entrypoint refusal at `python-sdk.dang:154-156` can go for git clients.

**`findClientRoot` (`python-sdk.dang:42`) [provisional].** The marker stays
`pyproject.toml`. A `pyproject.toml` with `[tool.dagger] generated-client` is a
member, so it is never a client root. The answer is the nearest
`pyproject.toml` above the member: the scope root. The `sdk/` lift stays for
modules that still vendor.

## 13. Engine dependency (property 4)

`contextModuleSource` is not in the committed bindings. The `beta.13` schema is
not checked. Tracked in `dagger/dagger#14148`.

| Case | Works without the engine change |
| --- | --- |
| Git client from a plain program | Yes |
| Git client from a module | Yes on Java's engine; not tested here |
| Local client from a plain program | Yes, `currentWorkspace.moduleSource(path)` from a client session |
| Local client from a module | No; interim `[[dependencies]]` |

Not a blocker. Only the last row waits for the engine.

## 14. Open questions

- **Q1. Namespace name.** `dagger_clients`, `dagger_modules`, other.
  Recommendation: `dagger_clients`.
- **Q2. Runtime source.** (a) a runtime member `clients/_runtime` in each scope;
  (b) publish now. Recommendation: (a) in phase 1, publish in phase 2 and remove
  the member. The PyPI name is Yves's call.
- **Q3. Session argument.** (a) keyword-only `session=`; (b) first positional;
  (c) `linter.using(session)`. Recommendation: (a).
- **Q4. Contributed field shape.** (a) `as_linter(binding)`; (b)
  `Linter.as_linter(binding)`. Recommendation: (a).
- **Q5. Global client: details and end of life.** [decided] A temporary global
  client exists behind a flag; existing modules get the flag, new modules do
  not. Still open: (1) flag name and place: recommendation
  `[tool.dagger] global-client = true`; (2) contributed fields: recommendation
  add them to core classes at run time; (3) end of life: recommendation a
  `DeprecationWarning` in phase 2, removal in a release Yves names.
- **Q6. `dagger.mod` and core.** (a) raw query builder; (b) a layer above core.
  Recommendation: (a).
- **Q7. Local clients in modules before `contextModuleSource`.** (a) keep
  `[[dependencies]]`; (b) refuse; (c) `currentWorkspace` from module code.
  Recommendation: (a). (c) builds on the #14148 hole.
- **Q8. A module that uses a client project outside its directory.** A module
  that declares its own clients needs nothing. Options: (a) `include` the client
  project; (b) refuse and tell the user to declare the clients on the module.
  Recommendation: (a) if a spike confirms it, else (b).
- **Q9. Who adds a client to `[project] dependencies`.** (a) the SDK in a module
  scope, never in other scopes; (b) always the user; (c) always the SDK.
  Recommendation: (a).
- **Q10. Removing a client.** [closed by revision 3] A scope owns its members;
  `generateScope` deletes the member, its source and its `members` entry, then
  relocks. Tested with uv.
- **Q11. Version check strictness.** Recommendation: client/core mismatch is an
  import error; engine/core mismatch is a warning in phase 1.
- **Q12. The members directory inside a scope.** [decided] The scope location
  is a convention; clients go inside the scope; the revision 2 `clientsPath`
  setting is dropped. Open: (a) `clients/<name>` in every scope; (b) the scope
  root for a client project and `clients/` for a module; (c) a setting.
  Recommendation: (a). A shorter suggested client project location is
  `.dagger/python`.

## 15. Checks

Invert each assertion once and confirm that it fails.

1. The digest of `clients/linter` is the same in a module scope and in a client project scope.
2. Every `dagger.*` module imports in a venv without `dagger_clients` and without `dagger_global`.
3. AST scan of imports in core, in each client package and in the runtime.
4. Module source that calls a client passes mypy and pyright, then `dagger call` runs it.
5. `uv build --wheel` for each member with no engine; the wheel exists and contains the package; it imports in a fresh venv.
6. A changed `CORE_DIGEST` makes a client import raise `StaleClientError`.
7. Fake engine: two calls in one session send one serve; two sessions send two.
8. A module with a git client and a local client loads both through the CLI.
9. Global client, existing module: generation writes `global-client = true`; the unchanged source passes mypy and runs through `dagger call`; `type(dag.container()) is dagger_clients.core.Container`.
10. Global client, new module: no `global-client` key, no `clients/_global`; `dag.container` raises the migration message.
11. Global client, turned off: `config set --global-client=false`, then generate; `clients/_global` and its dependency are gone.
12. Any location: a client project in another path; checks 1, 4 and 8 pass; `findClientRoot` inside a member answers with the scope root.
13. Install one: a consumer that names one member gets only that member, core and the runtime.
14. One scope per environment: a consumer that names members of two scopes fails with uv's conflict on `dagger-clients-core`.
15. Remove a client: its member, source and `members` entry are gone; `uv sync --locked` passes.
16. User content kept: user tables, comments and a user member in the scope `pyproject.toml` survive generation byte for byte.

## 16. Phases

**Phase 1: self-serving clients in workspace scopes, new modules first.** Remove
runtime imports of generated core. Add `Session`, `Target`, serve memo, error
types. Partition in the generator; emit the core member, one member per client,
and `clients/_global` with the flag. `generateScope` turns every scope into a uv
workspace root and manages its members. New modules use the new layout.
Existing modules get `global-client = true`. `findClientRoot` lifts a member to
its scope. `mod config` handles `global-client`. Git clients serve everywhere;
local clients serve from plain programs; local clients in modules use
`[[dependencies]]`. Spikes first: core alone; a workspace module in
`runtime/build.dang` (uv and pip modes); a format-keeping TOML editor; Q8
include; typing through `dagger_global`. Checks 1–16.

**Phase 2: one artifact everywhere.** After `contextModuleSource`: local clients
self-serve in modules; remove `[[dependencies]]`; allow clients with the static
entrypoint. Publish the runtime and remove `clients/_runtime`. The global client
warns on import; its removal date is Yves's call.

## 17. Not verified

- [speculative] How `generateScope` gets core alone. Fallback: strip module-owned
  types from a client schema, with Java's skew guard.
- [speculative] `beta.13` introspection carries `@sourceMap(module:)`.
- [speculative] The runtime build installs a module that is a uv workspace root,
  in uv and pip modes (`runtime/build.dang:167-189`).
- [speculative] A scope inside a tree that already has a uv workspace root above
  it (nested workspaces).
- [speculative] A consumer outside the scope that is itself a uv workspace root.
- [speculative] A module `include` can name a path above the module root (Q8 only).
- [speculative] mypy and pyright type `dagger.dag` as the global `Client`
  through the optional import.
- [speculative] `Module.serve` from a module session on this repo's engine.
- [speculative] Spec decision 4: a client function whose signature names a type
  from another client.

Verified by experiment with uv 0.12.13, on hand-written stand-ins for generated
members: namespace build; shared namespace across editable installs; mypy and
pyright errors across the namespace; a scope root with and without `[project]`;
a consumer inside and outside the scope installs only the member it names plus
core; `uv sync --locked --no-dev`; removal and relock; two scopes in one
environment fail on `dagger-clients-core`; one member's files are identical in
two scopes.
