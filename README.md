# python-sdk

A Dagger module for managing Dagger modules that use the Python SDK.

The Dagger CLI ships without built-in module-management commands like
`init` or `develop`. Those operations live in SDK-specific modules like this
one, called through `dagger call`.

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

Create a Python SDK module under the nearest `.dagger/modules/<name>/`:

```sh
dagger call python-sdk init --name my-module
```

Pick a different location:

```sh
dagger call python-sdk init --name my-module --path some/dir/my-module
```

Pick a starter template (`minimal` is the default; `legacy` gives you a
container-echo example):

```sh
dagger call python-sdk init --name my-module --template legacy
```

`init` only seeds template files. Run `mod ... generate` to produce the
generated SDK.

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

## Manage dependencies

List:

```sh
dagger call python-sdk mod --path my-module deps list
```

Add (run `mod ... generate` after to refresh generated SDK files):

```sh
dagger call python-sdk mod --path my-module \
    deps add --source github.com/some/module
```

Add with a custom local name:

```sh
dagger call python-sdk mod --path my-module \
    deps add --source github.com/some/module --name alias
```

Remove by name or source:

```sh
dagger call python-sdk mod --path my-module deps remove --name alias
```

Update one remote dependency, or all of them:

```sh
dagger call python-sdk mod --path my-module deps update
dagger call python-sdk mod --path my-module deps update --name some-dep
```

## Manage the required engine version

```sh
# Read the version pinned in dagger.json
dagger call python-sdk mod --path my-module engine required

# Pin to a specific version
dagger call python-sdk mod --path my-module engine require --version 0.20.8

# Pin to the engine version you're currently running
dagger call python-sdk mod --path my-module engine require-current

# Pin to "latest"
dagger call python-sdk mod --path my-module engine require-latest
```

## Discover modules in a workspace

```sh
# Every Python SDK module under the workspace
dagger call python-sdk modules path
```

See [`python-sdk.dang`](./python-sdk.dang) for the full type surface.

## Skipping generation

To exclude a directory tree from `generate-all`, drop an empty
`.dagger-python-sdk-skip-generate` file at or above the module root. Useful
for fixtures, vendored modules, or anything you don't want regenerated in bulk.

```sh
touch some/fixture/.dagger-python-sdk-skip-generate
```
