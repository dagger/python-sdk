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
| `entrypoint/` | the shared Dang `ModuleEntrypoint` this repository used to serve, frozen; see "Shared entrypoint" |

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
| `[entrypoint] source = <a path in the module>` | a Dang entrypoint of the user's own, kept as written |
| `[runtime] source = <a ref or path>` | a module runtime, which builds and runs the module itself |

This SDK needs an engine `v1.0.0-beta.18` or later, the first release that
loads a module's files through its entrypoint's `sources` member. An
entrypoint runs a module on the engine's own version, so an entrypoint
manifest keeps no `engineVersion`.

On an older engine, `dagger module init` and `dagger generate` stop before
they write anything, with a message that names the engine's version. A
generated entrypoint fails to load on an older engine with `field "types":
missing argument "workspace" required by interface`. To stay on an engine from
v1.0.0-beta.14 to v1.0.0-beta.17, pin `v1.1.1`, the last release that runs
there: `dagger module install github.com/dagger/python-sdk@v1.1.1`, and
`dagger.io/sdk/python/entrypoint@v1.1.1` for the shared entrypoint of that
release. An engine older than v1.0.0-beta.14 runs neither release, and needs
an upgrade.

An entrypoint served from elsewhere, such as the shared one this repository
used to serve as `dagger.io/sdk/python/entrypoint@v1`, a pin of it or a fork,
is replaced by the generated entrypoint at the module's next
`dagger generate`. A module moved this way needs an engine `v1.0.0-beta.18` or
later, which generation already requires.

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
  any module. A module on the generated entrypoint gets the libraries outside
  its directory that its `pyproject.toml` names as path sources (see "Files
  outside the module").
- A Dang entrypoint in the module's own files, named by a path, is kept as
  written. One served from elsewhere is replaced by the generated entrypoint.
- `[entrypoint]` and `[runtime]` together are refused, naming both: only one
  thing can run the module.
- `disableDefaultFunctionCaching` and `[codegen]` are refused one at a time:
  the first is a function's own `cache=` on an entrypoint, and the second is
  read by no entrypoint. A `[clients]` table is refused as well: a module's
  clients are its scope's, in `dagger.toml`.

An unmanaged legacy `dagger.json` with `"sdk": {"source": "python"}` still
resolves to the runtime built into the engine (`dagger/dagger`'s
`sdk/python`), which generates bindings at module load.

## The engine release a module targets

Each release of this SDK targets one engine release, `v1.0.0-beta.21` today.
Today that release is what the SDK announces and what the setting below
pins, not yet what the SDK generates core from:

- The generated entrypoint declares it to the engine: it sets
  `DAGGER_CLIENT_VERSION` on the exec that starts the module's process, next
  to `DAGGER_MODULE_CALL_DIGEST`. The engine ignores it today; a later engine
  serves that release's view to the process.
- Outside a module, the library vendored into `sdk/` passes
  `--version <release>` to the `dagger session` it starts, so the engine
  serves that release's view to the program, and provisions that release's
  CLI when none is on the system. A program attached to an existing session,
  through `DAGGER_SESSION_PORT`, announces nothing: it gets the session's
  version.
- `clients/core`, the global client and the dependency clients are still
  generated from the running engine, so an engine update can still change
  them, and an engine older than the targeted release is not refused: the
  first call to a function it lacks fails.

The scope setting `targetVersion`, under
`[sdks.python.scopes."<module path>".settings]` in `dagger.toml`, pins the
release, from this SDK's floor to the targeted release;
`dagger module init python --target-version <release>` writes it. Until core
comes from a published library, only the targeted release is accepted: any
other value stops generation before it writes anything, with a message that
names the module and says why.

Once the `dagger-clients-core` library is published for each release, core
will come from it for the targeted or pinned release: `dagger generate` will
write the same core whatever engine runs it, an SDK update will change the
targeted release and the core, and older pins will be accepted. Dependency
clients still come from loading the modules they name.

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

`entrypoint/` is the `ModuleEntrypoint`, written in Dang, that this repository
used to serve to every Python module as `dagger.io/sdk/python/entrypoint@v1`,
before each module got one generated into it. It is frozen: generation no
longer writes a manifest that names it, a module whose manifest names it, a
pin of it or a fork moves to the generated entrypoint at its next
`dagger generate`, and its copies of `runtime/build.dang` and
`runtime/workspace_reader.py` no longer follow the runtime's. It stays in the
repository, with the tags that serve it, so a module pinned to it in git
still loads: `@v1` selects the greatest `entrypoint/v1.*` tag, then the
greatest plain `v1.*` tag. The Python process it starts belongs to no module
on the engine's side, as with the static entrypoint.

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
| `main.dang` | the `ModuleEntrypoint`: returns the types, and runs calls in the module's container, declaring the targeted engine release to the engine |
| `build.dang` | the container build, copied from `runtime/build.dang` |
| `workspace_reader.py` | the workspace reader the build runs, copied from `runtime/workspace_reader.py` |

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

Generation writes an entrypoint into a module that has none, or whose manifest
names one served from elsewhere, and leaves a Dang entrypoint in the module's
own files alone, so a module on an entrypoint of its own stays there. A module
that names a runtime of its own keeps it. See
[`future/done/static-module-entrypoint.md`](./future/done/static-module-entrypoint.md)
for the design.

## Files outside the module

A generated entrypoint builds the module from its own directory and from the
libraries outside it that the build reads. Generation finds them where uv
needs them, in `pyproject.toml`:

```toml
[tool.uv.sources]
greet = { path = "../../libs/greet", editable = true }
```

`dagger generate` follows each path source outside the module directory, the
path sources of those in turn, and the workspace members outside the module,
and checks them against `uv.lock`. The `sources` function that it writes into
`sdk/entrypoint/main.dang` returns the module directory and the directories it
finds, and the engine loads them with the module: the build installs them, and
a change to a file in them gives a new cache key instead of a stale result. The
module also runs from a Git repository, where the engine reads the files at the
commit.

Generation refuses a path source that is absolute or leaves the workspace, and
a library directory that holds no file or only files that `.gitignore`
ignores. It also refuses an outside library on any entrypoint but the one
generated into the module; a module on a runtime lists such files under
`include` in `dagger-module.toml` instead.

Not loaded in this version:

- a file outside the module that the code opens at call time: the call fails
  with `FileNotFoundError`;
- a hatch `force-include` source outside a library: its build fails with
  "Forced include not found";
- a wheel, a source archive or a symlink outside the module as a path
  source: generation refuses it, so point the path source at a directory
  instead;
- a pip-mode module (`use-uv = false`), and a `-e ../x` line in
  `requirements.lock`.

A local module needs a Git repository above it to load a library outside it.
Run `dagger generate` again after a change to the path sources.

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
dagger module init python --name my-module --target-version v1.0.0-beta.21
dagger module init python --name my-module \
    --python-version 3.13 \
    --use-uv=false \
    --base-image python:3.13-slim
```

`--legacy-runtime` puts the module on the runtime this SDK publishes, as "How
a module runs" describes; without it, a new module gets an entrypoint
generated into it.

`--template` picks a starter template: `default` (a small working module) when
you pass nothing, or `empty` for a bare object class. `--target-version` pins
the engine release to generate for, as "The engine release a module targets"
describes. The three `pyproject.toml` flags are optional; by default the
template's Python version is used, uv is enabled, and no base image override
is written.

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
(`dagger module client add`). A client is declared on its scope in
`dagger.toml` and generated as a member of the scope under `clients/<name>/`;
`dagger-module.toml` lists no clients, and the member of a removed client is
deleted.
A client declared by a path within the workspace remains bound by that path
when the workspace is opened from Git.
Standalone clients, in a scope without a module, are not generated yet; adding
one to a Python scope is refused and the workspace is left unchanged.

## Test

```sh
dagger check
```

Each e2e check runs as an `engine-e-2-e` check of the same name, on the engine
of the floor release built from its dagger/dagger commit;
`engine-e-2-e/floor-coverage-check` checks that every e2e check runs this way,
and `engine-e-2-e/below-floor-check` checks that the release below the floor
is refused. AGENTS.md has the commands that run the e2e checks on the local
engine.
