# python-sdk

A Dagger module for managing Dagger modules that use the Python SDK.

SDK-specific module authoring (scaffolding new modules, language build config,
codegen) lives in modules like this one. Under the CLI 1.0 init contract the
engine drives the SDK: this module exposes `initModule` and `targetRuntime`,
and the engine merges the SDK-owned files with its own workspace bookkeeping.
Shared, language-agnostic operations — editing a module's dependencies or its
required engine version — are owned by the core CLI (`dagger module deps`,
`dagger module engine`) and are no longer part of this module's surface.

It uses the engine's native `Workspace` and `ModuleSource` APIs directly.

## What lives here

| Path | What it is |
| --- | --- |
| `python-sdk.dang`, `mod.dang`, `templates/` | authoring: `initModule`, `generate`, config, discovery |
| `client.dang`, `client-template/` | standalone clients: `generateClient`, `initClient` |
| `codegen.dang` | how both drive the code generator and vendor the library |
| `sdk/` | the `dagger-io` client library and code generator |
| `runtime/` | the module runtime the engine calls to run a module |

Code generation happens at `dagger generate`, through this module's `@generate`
hook, which runs the code generator in `sdk/` and vendors the result into the
module. The runtime never generates: it builds a module from its **committed**
generated files, so there is no codegen step in a cold `dagger call`, and a
module that has not been generated fails with an actionable error rather than
being silently regenerated.

Pre-1.0 `dagger.json` modules are the exception: they keep being generated and
run by the Python SDK baked into the engine, exactly as before.

## Two runtimes, one name

Python modules reach one of two implementations, and which one is decided by
the module's config format:

- **Legacy** — a `dagger.json` with `"sdk": {"source": "python"}` resolves to
  the runtime baked into the engine (`dagger/dagger`'s `sdk/python`), which
  still generates bindings at module load. Nothing about those modules changes,
  and they need no migration.
- **Modern** — a `dagger-module.toml` can point `[runtime] source` at this
  repository's `runtime/`, which is the no-codegen path above. Either a module
  ref or a path relative to the module works, for both `dagger generate` and
  `dagger call`.

The engine resolves the short name `python` to exactly one target, the
engine-baked runtime, so the modern path is reached by module ref rather than
by name. `targetRuntime` — what `dagger module init python` writes into a new
module — is therefore still `python` today; it moves to
`github.com/dagger/python-sdk/runtime` in a follow-up, once `runtime/` exists
on the default branch for that ref to resolve to. See
[`future/done/self-contained-python-sdk.md`](./future/done/self-contained-python-sdk.md)
for the full reasoning and for the engine change that would let one name serve
both.

### Trying this repository's runtime

`targetRuntime` still writes `python`, so a module created today runs on the
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

## Install

From your workspace root:

```sh
dagger install github.com/dagger/python-sdk
```

After install, the module is available in `dagger call` as `python-sdk`.

Calls that return a `Changeset` will print the diff and prompt you to confirm
before writing anything to your workspace.

## Create a new module

With a CLI that supports the 1.0 init contract, the engine dispatches to this
SDK's `initModule`:

```sh
dagger module init python my-module
```

`initModule` only seeds the SDK-owned template files; the engine writes the
module config and workspace entries. Run `generate` afterwards to produce the
generated SDK bindings.

The SDK-specific args below become typed flags on `dagger module init python`:

```sh
dagger module init python my-module --template legacy
dagger module init python my-module \
    --python-version 3.13 \
    --use-uv=false \
    --base-image python:3.13-slim
```

`--template` picks a starter template: `default` (a small working module) when
you pass nothing, `empty` for a bare object class, or `legacy` for a
container-echo example. The three `pyproject.toml` flags are optional; by
default the template's Python version is used, uv is enabled, and no base image
override is written.

You can also call the function directly for testing. `path` is required (the
engine supplies it in the dispatched path):

```sh
dagger call python-sdk init-module --name my-module --path .dagger/modules/my-module
```

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

For a single module:

```sh
dagger call python-sdk mod --path my-module generate
```

Generation vendors the client library into `<module>/sdk/` and, next to it,
everything the module can call:

```
<module>/sdk/src/dagger/client/gen.py          the core API, as a module sees it
<module>/sdk/src/dagger/clients/<self>.py      a client for the module itself
<module>/sdk/src/dagger/clients/<dep>.py       one client per declared dependency
```

See [Clients](#clients) for what a client is and how module code uses one.

For every Python SDK module in the workspace (skipping any with a
`.dagger-python-sdk-skip-generate` marker at or above the module root):

```sh
dagger call python-sdk generate-all
```

## Clients

A module's dependency and a standalone client are the same thing: generated
bindings for one module, plus a serve preamble that makes sure the module is
served in the session before the first query goes through them. Every module
under `sdk/src/dagger/clients/` is one such client, named after the module it
binds to — its final name, so a dependency declared with an alias gets a client
under the alias. Core types are shared: a `dagger.Container` returned by one
client is the same class everywhere.

A module calls a dependency, and itself, through the client's entry point — a
function named after the module, taking the module's constructor arguments and
an optional `client`:

```python
from dagger import function, object_type
from dagger.clients.app import app        # the module's own client
from dagger.clients.builder import builder  # a declared dependency


@object_type
class App:
    @function
    async def build(self) -> str:
        return await builder().build("main")

    @function
    async def twice(self) -> str:
        return (await app().build()) * 2   # a self call, through the engine
```

Inside a module the engine has already served the module's dependencies and
the module itself, so the preamble does nothing there. A self call goes
through the engine like any other call, so it gets function-level caching.

Generate first, then call: a symbol imported from the module's own client has
to exist in the last generated client, so add the function, generate, then
call it. A client's types are not a module's own types — a function returns
the module's `@object_type`, never `dagger.clients.app.App`.

### Standalone clients

The same client works from a Python project that is not a module. Register
one and let `dagger generate` produce it:

```sh
dagger api client init python clients/builder ./builder
```

This records the client in the workspace config, seeds `clients/builder/pyproject.toml`
(declaring the vendored library, so `uv sync` there just works) and generates
`clients/builder/sdk/`. Regenerate with `dagger generate`, or directly:

```sh
dagger call python-sdk generate-client --module ./builder --path clients/builder
```

`module` is a workspace path or a git ref. A local module this SDK manages is
generated first, so the client is never read off an ungenerated module.
Everything generated sits under `sdk/`; files next to it are yours.

```python
import dagger
from dagger.clients.builder import builder


async def main() -> None:
    async with dagger.connection():
        print(await builder().build("main"))
```

Outside a module the preamble serves the bound module the first time a query
runs, then remembers it for the session. A module bound by workspace path
needs a workspace — run from inside one, or pass `dagger.Config(workdir=...)`;
a module bound by git ref resolves anywhere. The vendored library carries
engine provisioning and the CLI version of the engine the client was
generated against.

### Migrating a module

Modules on the `dagger-module.toml` config get clients on their next
`dagger generate`; `dag.<dependency>()` is gone. For each dependency:

```python
# before
from dagger import dag
await dag.builder().build("main")

# after
from dagger.clients.builder import builder
await builder().build("main")
```

A dependency that adds a function to a core type moves the same way: the
function is no longer a method on the core object, it is a module-level
function taking that object first. Its name is always its parent's name and
the field's — only the entry point is bare — so `Directory.asHello` is
`directory_as_hello`, and adding `File.asHello` next to it renames nothing:

```python
# before
await directory.as_hello().greet("world")

# after
from dagger.clients.hello import directory_as_hello
await directory_as_hello(directory).greet("world")
```

Legacy `dagger.json` modules are untouched: they keep the merged `gen.py` the
engine's builtin Python SDK generates.

## Manage dependencies and the engine version

Editing a module's dependencies or its required engine version is identical
across SDKs, so the core CLI owns it:

```sh
dagger module deps add github.com/some/module
dagger module engine require-latest
```

## Discover modules in a workspace

```sh
# Every Python SDK module under the workspace
dagger call python-sdk modules path
```

> [!NOTE]
> `modules` and `generate-all` read the modules registered under
> `modules.<sdk>.as-sdk.modules`, which the engine owns and narrows to the
> caller's cwd. Nothing scans module config files.

See [`python-sdk.dang`](./python-sdk.dang) for the full type surface.

## Skipping generation

To exclude a directory tree from `generate-all`, drop an empty
`.dagger-python-sdk-skip-generate` file at or above the module root. Useful
for fixtures, vendored modules, or anything you don't want regenerated in bulk.

```sh
touch some/fixture/.dagger-python-sdk-skip-generate
```
