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
| `runtime/` | the module runtime the engine calls to run a module, and the container build the static entrypoint shares |

Code generation happens at `dagger generate`, which calls `generateScope` for
every recorded scope. It runs the code generator in `sdk/` and vendors the
result into the module. The runtime never generates: it builds a module from its
**committed** generated files, so there is no codegen step in a cold
`dagger call`, and a module that has not been generated fails with an
actionable error rather than being silently regenerated.

When a managed pre-1.0 `dagger.json` scope is generated, the SDK writes
`dagger-module.toml` and removes `dagger.json`. An unmanaged legacy module keeps
using the Python SDK that is built into the engine.

## Two runtimes, one name

Python modules reach one of two implementations, and which one is decided by
the module's config format:

- **Legacy** — an unmanaged `dagger.json` with `"sdk": {"source": "python"}`
  resolves to the runtime built into the engine (`dagger/dagger`'s
  `sdk/python`). It still generates bindings at module load.
- **Modern** — a `dagger-module.toml` can point `[runtime] source` at this
  repository's `runtime/`, which is the no-codegen path above. Either a module
  ref or a path relative to the module works, for both `dagger generate` and
  `dagger call`.

The engine resolves the short name `python` to exactly one target, the
engine-baked runtime, so the modern path is reached by module ref rather than
by name. The manifest `generateScope` writes for a new module therefore still
names `python`; it moves to `github.com/dagger/python-sdk/runtime` in a
follow-up, once `runtime/` exists on the default branch for that ref to
resolve to. See
[`future/done/self-contained-python-sdk.md`](./future/done/self-contained-python-sdk.md)
for the full reasoning and for the engine change that would let one name serve
both.

### Trying this repository's runtime

A module created today names the `python` runtime, so it runs on the
engine's runtime. To move one onto this repository's runtime, point it there by
hand:

```toml
# <module>/dagger-module.toml
[runtime]
source = "github.com/dagger/python-sdk/runtime"
```

Then `dagger generate` the module and `dagger call` it as usual. The generated
files are identical either way — generation is this SDK's regardless of which
runtime runs the module — so switching back is just editing the line again.

Within this repository, a path relative to the module works too, which is how
the end-to-end fixture exercises the runtime before the ref exists.

## Static entrypoint

By default a module's types are discovered by running it: the engine builds
the module's container and starts Python once per session to register the
types, then again for every call. With `--static-entrypoint` the types are
computed once, at `dagger generate`, and written into a generated entrypoint
the engine loads without running Python:

```sh
dagger module init python --name my-module --static-entrypoint
```

Generating the module then writes a manifest version 2 instead of a runtime
manifest, and `sdk/entrypoint/` next to the vendored library:

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
module clients (manifest version 2 has no dependencies), any `cache=` value
on a function (the entrypoint's exec is content-cached and receives no
per-call signal), the `legacy` template, and a manifest with `include`,
`disableDefaultFunctionCaching`, a runtime other than `python`, a `source`
other than `.`, or `codegen`, `clients` or `dependencies` tables. Such
modules keep the default path. There is no `debug` terminal on the static
path, and a function error reaches the caller as the exec failure with the
process's stderr.

The setting is persisted on the scope. To switch an existing module either
way, re-run `dagger module init python --path <module>` with
`--static-entrypoint` or `--static-entrypoint=false`, or edit the scope's
settings in `dagger.toml`, then `dagger generate`. Generating one module
directly, with `dagger call python-sdk mod --path <module> generate`, keeps
the mode that module is in; switching modes goes through
`dagger module init python --path <module>` as above. Switching back removes
`sdk/entrypoint/` and rewrites a runtime manifest with the generating
engine's version. Loading a static module needs an engine that reads
manifest version 2 (dagger/dagger#14038); see
[`future/static-module-entrypoint.md`](./future/static-module-entrypoint.md)
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
