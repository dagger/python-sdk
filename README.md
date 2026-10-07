# python-sdk

A Dagger module for managing Dagger modules that use the Python SDK.

SDK-specific module authoring (scaffolding new modules, language build config,
codegen) lives in modules like this one. The engine drives the SDK
(dagger/dagger#13992): it records a module scope in `dagger.toml`, sets the
workspace cwd to it, and asks this module to generate the complete scope
through `findClientRoot` and `generateScope`. The module writes the manifest and
its own files; the engine owns the workspace bookkeeping.

It uses the engine's native `Workspace` and `ModuleSource` APIs. It uses
`sdkHelpers.moduleManifest` from `dagger/sdk-helpers`.

## What lives here

| Path | What it is |
| --- | --- |
| `python-sdk.dang`, `mod.dang`, `templates/` | authoring: `findClientRoot`, `generateScope`, `mod` (generate, config), templates |
| `sdk/` | the `dagger-io` client library and code generator |
| `runtime/` | the container build both entrypoints share, and a module runtime for a manifest that names it |
| `entrypoint/` | the shared Dang `ModuleEntrypoint`, served from this repository to any module that names it |

Code generation happens at `dagger generate`, which calls `generateScope` for
every recorded scope. It runs the code generator in `sdk/` and vendors the
result into the module. The runtime never generates: it builds a module from its
**committed** generated files, so there is no codegen step in a cold
`dagger call`, and a module that has not been generated fails with an
actionable error rather than being silently regenerated.

When a managed pre-1.0 `dagger.json` scope is generated, the SDK writes
`dagger-module.toml` and removes `dagger.json`. An unmanaged legacy module keeps
using the Python SDK that is built into the engine.

## How a module runs

A module's manifest names what runs it, and generation follows the manifest.
There are three answers, and a new module gets the first:

| `dagger-module.toml` | What runs the module |
| --- | --- |
| `[entrypoint] source = "./sdk/entrypoint"` | the entrypoint generated into it, with its types baked |
| `[entrypoint] source = <a ref>` | an entrypoint served from elsewhere, the shared one below among them |
| `[runtime] source = <a ref or path>` | a module runtime, which builds and runs the module itself |

An entrypoint needs an engine that runs Dang entrypoints and serves
`serveModule`: `v1.0.0-beta.14` or later. It runs a module on the engine's own
version, so an entrypoint manifest keeps no `engineVersion`.

Generating a module that has a manifest already keeps the kind it names:

- `[runtime] source = "python"`, the runtime built into the engine, which this
  SDK wrote before, is replaced by a generated entrypoint, and `engineVersion`
  and `[[dependencies]]` go with it. A module that cannot run on an entrypoint
  moves to the runtime this SDK publishes instead, on the version the generator
  reads core in.
- A `[runtime]` of the user's own is kept as written, with no entrypoint added:
  the engine follows an entrypoint over a runtime, so adding one would take the
  runtime out of the picture. `runtime/` is such a runtime, named by module ref
  or by a path relative to the module.
- A manifest that says which files the module is (`include`, `exclude`) or roots
  it elsewhere (`source` other than `.`) stays on a runtime: an entrypoint takes
  the module's own directory and all of it, and dropping the setting would
  change which files the module is. The runtime it stays on is the one this SDK
  publishes, `dagger.io/sdk/python/runtime@v1`, since the runtime built into the
  engine reads the layout before this one; `--legacy-runtime` asks for it for
  any module.
- An entrypoint of another kind, a pin of the shared one or a fork, is kept as
  written. `--remote-entrypoint` asks for the shared one instead.
- `--legacy-runtime` and `--remote-entrypoint` cannot be set together: each asks
  for a different way to run the module, and a manifest names one.
- `[entrypoint]` and `[runtime]` together are refused, naming both: only one
  thing can run the module.
- `disableDefaultFunctionCaching` and `[codegen]` are refused one at a time:
  the first is a function's own `cache=` on an entrypoint, and the second is
  read by no entrypoint. A `[clients]` table is refused as well: a module's
  clients are its scope's, in `dagger.toml`.

An unmanaged legacy `dagger.json` with `"sdk": {"source": "python"}` still
resolves to the runtime built into the engine (`dagger/dagger`'s
`sdk/python`), which generates bindings at module load.

## Migrate a module

Migrating takes two commands per module: the engine converts the module's
config, and this SDK generates its scope. Run both from the workspace root,
and take the modules in dependency order, each dependency before the modules
that call it.

1. Install this SDK. The command creates `dagger.toml` if the workspace has
   none:
   ```sh
   dagger module install github.com/dagger/python-sdk
   ```
2. Convert one module's `dagger.json` and record its scope:
   ```sh
   dagger module migrate <path> -y
   ```
3. Generate that scope, before migrating anything that depends on this module:
   ```sh
   dagger generate -y
   ```

Step 2 writes `dagger-module.toml` keeping the legacy shape (`engineVersion`,
`[runtime] source = "python"`, `[[dependencies]]`), removes `dagger.json`, and
records `[sdks.python.scopes."<path>"]` in `dagger.toml` with the module's
dependencies as its clients. Step 3 is what moves the module to this layout:
the manifest keeps only `name` and `[entrypoint]`, the dependencies become
generated clients, the SDK files are written into the module, and the version
the pre-1.0 config declared goes, since an entrypoint runs the module on the
engine's own version.

Keep steps 2 and 3 paired, in that order. Converting a module loads its
dependencies as modules, and a dependency that was converted but not yet
generated has no generated files for a runtime to build: that fails with
`generated file "sdk/pyproject.toml" is missing`, naming the dependency.

`dagger workspace migrate` is not a shortcut for the whole repository. It
migrates the workspace config and the modules the workspace installs; a module
of your own is an optional candidate, which it lists as
`dagger module migrate <path>` and then skips, so it can report
"No migration needed" while every module is still pre-1.0. Select them
explicitly, with `--module <path>` repeated, or take them one at a time as
above.

A client loads its target with `serveModule`, which asks the engine for a
module by its manifest, so a target that still has only a `dagger.json` cannot
be served; generation names the target and the command to convert it.

Two shapes keep a module on a runtime rather than an entrypoint: `include` or
`exclude`, which an entrypoint cannot honour because it takes the module's own
directory and all of it, and a `source` other than `.`, which puts the module's
code elsewhere. Generation moves such a module from the runtime built into the
engine, which reads the layout before this one, to the runtime this SDK
publishes, which builds this one:

```toml
# <module>/dagger-module.toml
name = "my-module"
engineVersion = "v1.0.0-0"
include = ["../shared-package"]

[runtime]
  source = "dagger.io/sdk/python/runtime@v1"
```

A module reference is a legal `[runtime] source`, and so is a path relative to
the module, which is how this repository's e2e checks point at `runtime/` in the
working tree. A module whose `source` is not `.` needs more than the move:
generation writes the scope beside the manifest, and the module builds from
`source`.

Code written against the layout before calls `dag` and `dagger.Container`.
Generation keeps that code working by writing the temporary global client,
`sdk/src/dagger_global/`, and `global-client = true` under `[tool.dagger]`,
when the module is one the builtin runtime ran: its vendored bindings say so
(`sdk/src/dagger/client/gen.py` or `src/dagger_gen.py` with the generator's
header), or its manifest does (a pre-1.0 `dagger.json`, or `[runtime] source =
"python"`). A scope file that already sets `global-client = false` keeps it
off. The global client is temporary: move the code to
`from dagger_clients.core import ...` and turn the flag off.

## Shared entrypoint

`entrypoint/` is one `ModuleEntrypoint`, written in Dang, that backs every
Python module at once, with nothing generated into the module. A module whose
manifest names it keeps it:

```toml
# <module>/dagger-module.toml
name = "my-module"

[entrypoint]
kind = "dang"
source = "dagger.io/sdk/python/entrypoint@v1"
```

A Dang entrypoint already in the manifest is kept as written, so a module can
pin a version of the shared entrypoint, point at a fork, or name one of its
own. Generation replaces only the static entrypoint it writes itself, told by
its source, `./sdk/entrypoint`. The manifest is read with a TOML parser, so
quoting and key order are the user's.

Inside an entrypoint `currentModule` is the module it serves, so the
entrypoint builds that module's container from `currentModule.source`, with
the same build the runtime uses, however the module was loaded. It asks the
module to describe itself (`python -m dagger.mod describe`) or to run one call
(`python -m dagger.mod call`). The types it returns are rebuilt from that
description in the engine's own session.

The module's code runs in an exec the entrypoint starts, and loads a client
with `serveModule`, as a plain program does. The engine resolves a local
client's path in the tree the module was loaded from: its git repository at
the pinned commit, the directory it was built from, or on the host its git
repository, or its own directory outside one. An engine without that resolves the path in the caller's
workspace instead, and can serve the wrong module.

| File | What it is |
| --- | --- |
| `main.dang` | the `ModuleEntrypoint`: `types` and `call` |
| `build.dang` | the container build, generated from `runtime/build.dang` |

`build.dang` is generated, not hand-edited: the engine copies only the `.dang`
files at the top of an entrypoint directory, and `currentModule` inside an
entrypoint is the module it serves, so a shared entrypoint can read none of its
own non-Dang files. The externals block that reads `runtime/images/` is written
out into the copy. `dagger check -m .dagger/modules/e2e` fails when the copy
drifts; refresh it with
`dagger call -m .dagger/modules/e2e shared-entrypoint-build export --path entrypoint/build.dang`.

The address above only resolves once `entrypoint/` is on this repository's
default branch and a `v1` release is tagged: `@v1` selects the greatest
`entrypoint/v1.*` tag, then the greatest plain `v1.*` tag. The Python process the entrypoint starts belongs to no module
on the engine's side: the core API works in it, and `dag.current_module()`
fails with "no current module". The static entrypoint has the same limit.

## Static entrypoint

On the shared entrypoint a module's types are discovered by running it: the
engine builds the module's container and starts Python once per session to
register the types, then again for every call. A generated entrypoint computes
them once, at `dagger generate`, and the engine loads them without running
Python. Every new module gets one:

```sh
dagger module init python --name my-module
```

Its manifest names that entrypoint, and `sdk/entrypoint/` sits next to the
SDK files:

| File | What it is |
| --- | --- |
| `types.dang` | the module's types, as a literal list of `TypeDef` values |
| `main.dang` | the `ModuleEntrypoint`: returns the types and runs calls in the module's container |
| `build.dang` | the container build, copied from `runtime/build.dang` |

The types come from importing the module in its own container and reading
what its decorators registered, so they are the ones the runtime would
register. They are baked, so a change that alters them — a new function, a
changed argument or return type — is served only after `dagger generate`;
`dagger generate` in CI, which must leave no diff, is what catches a module
that forgot it. A change the types do not describe, such as a function body,
runs as edited.

What a generated entrypoint cannot carry is in the list above: a module whose
manifest says which files it is, or names a runtime, keeps that runtime
instead. There is no `debug` terminal on this path, and a function error
reaches the caller as the exec failure with the process's stderr.

A module keeps the kind of entrypoint its manifest names: generation writes
one into a module that has none, and leaves a Dang entrypoint of another kind
alone, so a module on the shared entrypoint or on a fork stays there.
`--remote-entrypoint` asks for the shared one instead, for a new module or for
one that has an entrypoint of its own, and then `sdk/entrypoint/` goes. A
module that names a runtime of its own keeps it, with or without the flag. See
[`future/done/static-module-entrypoint.md`](./future/done/static-module-entrypoint.md)
for the design.

## Install

From your workspace root:

```sh
dagger module install github.com/dagger/python-sdk
```

The engine recognizes the SDK interface and records the module as the `python`
SDK in `dagger.toml`. After install, the module is also available in
`dagger call` as `python-sdk`.

Calls that return a `Changeset` will print the diff and prompt you to confirm
before writing anything to your workspace.

## Create a new module

```sh
dagger module init python --name my-module
```

The engine records the module scope in `dagger.toml` and calls this SDK's
`generateScope`, which renders the template, writes `dagger-module.toml`, and
generates the SDK bindings in one step.

The SDK settings below become typed flags on `dagger module init python` and
are persisted on the scope:

```sh
dagger module init python --name my-module --template empty
dagger module init python --name my-module --legacy-runtime
dagger module init python --name my-module --remote-entrypoint
dagger module init python --name my-module \
    --python-version 3.13 \
    --use-uv=false \
    --base-image python:3.13-slim
```

`--legacy-runtime` and `--remote-entrypoint` pick what runs the module, as
"How a module runs" describes; without either, a new module gets an entrypoint
generated into it.

`--template` picks a starter template: `default` (a small working module) when
you pass nothing, or `empty` for a bare object class. The three
`pyproject.toml` flags are optional; by default the template's Python version
is used, uv is enabled, and no base image override is written.

## Configure an existing module

Read the current configuration. Settings that are not explicitly written to
`pyproject.toml` are reported as `null` rather than guessed:

```sh
dagger call python-sdk mod --path my-module config get
```

Select a single value:

```sh
dagger call python-sdk mod --path my-module config get python-version
dagger call python-sdk mod --path my-module config get use-uv
dagger call python-sdk mod --path my-module config get base-image
```

Change one or more values at once (prints a diff to confirm before writing).
Each flag is optional; omitting one leaves that setting untouched:

```sh
dagger call python-sdk mod --path my-module config set \
    --python-version 3.13 \
    --use-uv=false \
    --base-image python:3.13-slim
```

## Generate SDK files

`dagger generate` regenerates every recorded scope. A recorded module can also
be generated on its own:

```sh
dagger call python-sdk mod --path my-module generate
```

`mod` resolves recorded modules by default. For a module root that is not
recorded, pass the module root as `--path` and add `--find-up=false`:

```sh
dagger call python-sdk mod --path my-module --find-up=false generate
```

## Module clients

Module dependencies are replaced by generated module clients
(`dagger module client add`). In a module scope the client set becomes the
module's dependency set: each client is recorded in `dagger-module.toml` and
its types are part of the generated bindings, and a removed client is dropped
again.
Standalone clients, in a scope without a module, are not generated yet; adding
one to a Python scope is refused and the workspace is left unchanged.

## Test

```sh
dagger check
```

`engine-e-2-e:dev-sdk-check` builds the pinned dagger/dagger#13992 engine. It
runs the SDK interface checks, initializes Python modules with default and
explicit settings, and calls a generated module.
