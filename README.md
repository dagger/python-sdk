# python-sdk

A Dagger module for managing Dagger modules that use the Python SDK.

SDK-specific module authoring (scaffolding new modules, language build config,
codegen) lives in modules like this one. Under the CLI 1.0 init contract the
engine drives the SDK: this module exposes `initModule` and `targetRuntime`,
and the engine merges the SDK-owned files with its own workspace bookkeeping.
Shared, language-agnostic operations — editing a module's dependencies or its
required engine version — are owned by the core CLI (`dagger module deps`,
`dagger module engine`) and are no longer part of this module's surface.

Backed by [`github.com/dagger/sdk-sdk/polyfill`](https://github.com/dagger/sdk-sdk/tree/main/polyfill).

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

`--template` picks a starter template (`minimal` is the default; `legacy` gives
you a container-echo example). The three `pyproject.toml` flags are optional; by
default the template's Python version is used, uv is enabled, and no base image
override is written.

You can also call the function directly for testing. `path` is required (the
engine supplies it in the dispatched path):

```sh
dagger call python-sdk init-module --name my-module --path .dagger/modules/my-module
```

## Configure workspace defaults

Set SDK defaults once per workspace and have them apply to the modules you
create. List the settings this SDK exposes:

```sh
dagger settings python-sdk
```

Set a default (the SDK's constructor arguments are the settings; camelCase maps
to kebab-case on the CLI):

```sh
dagger settings python-sdk default-python-version 3.13
dagger settings python-sdk default-use-uv false
dagger settings python-sdk default-base-image python:3.13-slim
```

These act as fallbacks for `initModule`: when you create a module without the
matching `dagger module init python` flag (`--python-version`, `--use-uv`,
`--base-image`), the workspace default is applied. An explicit per-init flag
always wins.

> [!NOTE]
> Settings are stored under `[modules.python-sdk.settings]` in `dagger.toml`,
> are discoverable and typed today, and populate the SDK constructor on the
> `dagger call python-sdk …` path. Inheriting them through the privileged
> `dagger module init python` dispatch additionally requires the engine to
> thread `[modules.python-sdk.settings]` into the SDK at init time.

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

For every Python SDK module in the workspace (skipping any with a
`.dagger-python-sdk-skip-generate` marker at or above the module root):

```sh
dagger call python-sdk generate-all
```

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
> `modules` and `generate-all` discover modules by scanning legacy
> `dagger.json` files for `sdk.source == "python"`. This is obsolete for
> workspace-managed modules, where the engine owns the
> `modules.<sdk>.as-sdk.modules` source of truth.

See [`python-sdk.dang`](./python-sdk.dang) for the full type surface.

## Skipping generation

To exclude a directory tree from `generate-all`, drop an empty
`.dagger-python-sdk-skip-generate` file at or above the module root. Useful
for fixtures, vendored modules, or anything you don't want regenerated in bulk.

```sh
touch some/fixture/.dagger-python-sdk-skip-generate
```
