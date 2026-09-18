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

A module this SDK generates runs on a Dang entrypoint and on nothing else:
the shared one below, or with `--dang-entrypoint` one generated into the
module. Its manifest names no `[runtime]`. That needs an engine that runs
Dang entrypoints and serves `serveModule`: `v1.0.0-beta.14` or later.

Generating a module that has a manifest already:

- `[runtime] source = "python"`, which this SDK wrote before, is replaced by
  the entrypoint, and `engineVersion` and `[[dependencies]]` go with it. An
  entrypoint runs a module on the engine's own version.
- Any other `[runtime]` is the user's choice, and the manifest keeps it as
  written, with no entrypoint added: the engine follows an entrypoint over a
  runtime. `runtime/` is such a runtime, named by module ref or by a path
  relative to the module.
- `include`, `exclude`, or a `source` other than `.` next to the builtin
  runtime are refused, because an entrypoint manifest cannot carry them and
  dropping them would change which files the module is.

An unmanaged legacy `dagger.json` with `"sdk": {"source": "python"}` still
resolves to the runtime built into the engine (`dagger/dagger`'s
`sdk/python`), which generates bindings at module load.

## Shared entrypoint

`entrypoint/` is one `ModuleEntrypoint`, written in Dang, that backs every
Python module at once, with nothing generated into the module. `dagger
generate` names it in the manifest of every module that does not use
`--dang-entrypoint`:

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
quoting and key order are the user's. One exception is refused rather than kept: an
entrypoint this SDK does not write, in a scope with a local client, that does
not hand clients over. A local client loads only through what an entrypoint
hands the module (below), so generation stops and says so instead of writing
a module that fails at its first call. A fork hands them over by carrying
`handover.dang` unchanged and sending
`clients: ClientHandover(workspace: workspace).clients…` with each call, as
`main.dang` does; generation reads the entrypoint where the engine does and
accepts it then.

Inside an entrypoint `currentModule` is the module it serves, so the
entrypoint builds that module's container from `currentModule.source`, with
the same build the runtime uses, however the module was loaded. It asks the
module to describe itself (`python -m dagger.mod describe`) or to run one call
(`python -m dagger.mod call`). The types it returns are rebuilt from that
description in the engine's own session.

The module's code runs in an exec the entrypoint starts, which the engine does
not make the module: its own current workspace is the one found in its
container, so a client to a local module cannot resolve its path there. The
entrypoint resolves the clients the caller's `dagger.toml` declares on the
module's scope, and each call carries them by name, each as a module source
over only the files the engine loaded for that client. A client to a local
module loads through its entry (`node(id:)` → `asModule` → `serve`); a git
client goes through `serveModule`, as in a plain program.

The caller's workspace never reaches the module's code: an ID is a
capability, and a module is third-party code. Not the workspace, and not the
source `Workspace.moduleSource` returns either, because that one reloads its
context from the workspace when asked for more files. A function that
declares a `Workspace` parameter still gets one, because its caller passes it.

| File | What it is |
| --- | --- |
| `main.dang` | the `ModuleEntrypoint`: `types` and `call` |
| `handover.dang` | the declared clients each call carries; a static entrypoint carries a copy |
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

By default a module's types are discovered by running it: the engine builds
the module's container and starts Python once per session to register the
types, then again for every call. With `--dang-entrypoint` the types are
computed once, at `dagger generate`, and written into a generated entrypoint
the engine loads without running Python:

```sh
dagger module init python --name my-module --dang-entrypoint
```

Generating the module then names that entrypoint in the manifest instead of
the shared one, and writes `sdk/entrypoint/` next to the vendored library:

| File | What it is |
| --- | --- |
| `types.dang` | the module's types, as a literal list of `TypeDef` values |
| `main.dang` | the `ModuleEntrypoint`: returns the types and runs calls in the module's container |
| `build.dang` | the container build, copied from `runtime/build.dang` |

The types come from importing the module in its own container and reading
what its decorators registered, so they are the ones the runtime would
register. `main.dang` bakes a content digest of every source file that can
change them; a call after an edit is refused with a message to run
`dagger generate`. A lock file added afterwards is refused the same way, and
only file contents count, not permissions.

What the static path cannot do yet, and refuses at `dagger generate`:
any `cache=` value
on a function (the entrypoint's exec is content-cached and receives no
per-call signal), the `legacy` template, and a manifest with `include`,
`disableDefaultFunctionCaching`, a runtime other than `python`, a `source`
other than `.`, or `codegen`, `clients` or `dependencies` tables. Such
modules keep the default path. There is no `debug` terminal on the static
path, and a function error reaches the caller as the exec failure with the
process's stderr.

The setting is persisted on the scope. To switch an existing module either
way, re-run `dagger module init python --path <module>` with
`--dang-entrypoint` or `--dang-entrypoint=false`, or edit the scope's
settings in `dagger.toml`, then `dagger generate`. Generating one module
directly, with `dagger call python-sdk mod --path <module> generate`, keeps
the mode that module is in; switching modes goes through
`dagger module init python --path <module>` as above. Switching back removes
`sdk/entrypoint/` and names the shared entrypoint again. See
[`future/done/static-module-entrypoint.md`](./future/done/static-module-entrypoint.md)
for the design and the plan to make it the default.

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
dagger module init python --name my-module --template legacy
dagger module init python --name my-module \
    --python-version 3.13 \
    --use-uv=false \
    --base-image python:3.13-slim
```

`--template` picks a starter template: `default` (a small working module) when
you pass nothing, `empty` for a bare object class, or `legacy` for a
container-echo example. The three `pyproject.toml` flags are optional; by
default the template's Python version is used, uv is enabled, and no base image
override is written.

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
