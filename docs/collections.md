# Collections

A collection is a set of objects that a module discovers at run time, each
with a unique key. This SDK needs an engine v1.0.0-beta.26 or later. The
engine serves collections from v1.0.0-beta.15
([dagger/dagger#14221](https://github.com/dagger/dagger/pull/14221)), and
v1.0.0-beta.17 adds the projected types every `dagger call` into a collection
resolves ([dagger/dagger#14447](https://github.com/dagger/dagger/pull/14447))
and item selectors on `dagger call`
([dagger/dagger#14590](https://github.com/dagger/dagger/pull/14590),
[dagger/dagger#14602](https://github.com/dagger/dagger/pull/14602)).
v1.0.0-beta.22 removes the `batch` operation
([dagger/dagger#14802](https://github.com/dagger/dagger/pull/14802)).

## Declare a collection

Stack `@collection` under `@object_type`. The other order works too. A
collection has two members:

- A keys field, declared with `keys()`. Its type is a list of a scalar or an
  enum, such as `list[str]`, and it holds one unique key per item.
- A get function, a `@function` marked `@get`. It takes one argument of the
  key type and returns the item for that key.

Without the markers, the engine looks for members named `keys` and `get`.

```python
import dataclasses

from dagger import collection, delta, field, function, get, keys, object_type
from dagger_clients.core import CollectionDelta


@object_type
class Item:
    name: str = field()


@object_type
@collection
class Items:
    names: list[str] = keys()
    selection: CollectionDelta | None = delta()

    @function
    @get
    def lookup(self, name: str) -> Item:
        return Item(name=name)

    @function
    def selected(self) -> list[str]:
        return self.names

    @function
    def change(self, names: list[str]) -> "Items":
        return dataclasses.replace(self, names=names)


@object_type
class Shelf:
    @function
    def items(self) -> Items:
        return Items(names=["b", "a", "c"])
```

`CollectionDelta` is a core type: import it from `dagger_clients.core`, like
`Directory` or `Container`.

## What callers see

The engine replaces the collection's API with the same functions on every
collection:

| Function | Returns |
|---|---|
| `keys` | The keys, in the order the module returned them |
| `list` | Every item |
| `get` | The item with the given key |
| `subset` | A collection of the items with the given keys |

The collection keeps its other functions. The engine calls them on a copy of
the collection whose keys field holds only the selected keys. In the example,
`selected` returns those keys.

A check or generator on the item type runs once per selected item.

A client generated from a module with a collection has the same functions.
The Python client names `list` as `list_`, so it does not shadow the builtin:

```python
from dagger_clients.shelf import shelf

items = shelf().items()
names = [await item.name() for item in await items.list_()]
selected = await items.subset(["c"]).selected()
```

## Select items on the command line

The CLI adds a selector flag for each item type. The flag is named after the
module and the item type, so `Item` in the `shelf` module adds `--shelf-item`:

```shell
dagger call items name --shelf-item=a
dagger call --shelf-item=a name
dagger call items subset --keys=a,c selected
dagger call items get --key=a name
```

A selector after the collection selects within it. A selector before the first
function starts the pipeline at the item. `get` and `subset` remain available
by name, and `subset` selects several items for the collection's other
functions.

When more than one function returns the collection, each flag also names its
function. If `items` and `others` both return `Items`, the flags are
`--shelf-items-item` and `--shelf-others-item`, and `--shelf-item` is an
unknown flag.

The help of a command after the collection lists the flags under `SELECTION`,
for example `dagger call items selected --help`. `dagger call --help` does not
list them.

`dagger check` takes the flags for a module that the workspace registers. A
check `verify` on `Item` runs once for each selected item:

```shell
dagger check --shelf --check=verify --shelf-item=a --shelf-item=b
```

With `-m`, the flags are unknown. Put the selectors in the check's link instead:

```shell
dagger check -m ./shelf 'items/verify?shelf-item=a&shelf-item=b'
```

## Collection delta

A field declared with `delta()`, or named `delta`, receives the changes since
the module created the collection. Its type is `CollectionDelta | None`. The
engine fills it before each call. `await self.selection.added_keys()` and
`await self.selection.removed_keys()` return the keys added and removed. Both
are empty when the collection holds every item the module returned.

The delta keys are text, whatever the key type. An `int` key `2` reads `"2"`.
An enum key reads its member name, such as `"RED"`, not its value.

## Base state

The engine compares the current keys with the keys of the collection's base,
the collection the module first returned. The SDK carries the base as private
state on the collection, outside the module's schema. A copy keeps the
base: `dataclasses.replace`, `copy.copy`, `copy.deepcopy`, and an object that
a function mutates and returns. A new instance starts a new base. In the
example, the collection that `change(names=["c", "d"])` returns has a delta
with `d` added and `b` and `a` removed.

## Entrypoints

The module description carries the markers. The shared entrypoint, the
static entrypoint that `dagger generate` writes, and the runtime all pass them
to the engine. The shared entrypoint also loads a module whose vendored SDK
predates collections: that module declares no collection.
