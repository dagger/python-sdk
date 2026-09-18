# Collections

Requires the engine changes in [dagger/dagger#14221](https://github.com/dagger/dagger/pull/14221).

A collection has stored keys and a function that returns one item for a key.
The engine supplies `keys`, `get`, `list`, and `subset`. Other exposed
functions appear under `batch`.

```python
import dagger
from dagger import collection, delta, field, function, get, keys, object_type

@object_type
class Item:
    name: str = field()

@collection
@object_type
class Items:
    paths: list[str] = keys(default=list)
    selection: dagger.CollectionDelta | None = delta()

    @function
    @get
    def item(self, key: str) -> Item:
        return Item(name=key)
```

The markers are part of the module description. Both the shared entrypoint and
the generated static entrypoint pass them to the engine.

The engine fills the optional delta field before a module call. It compares the
current keys with the original keys. Copies preserve the internal base state.
A new object starts a new base. The internal state is not an exposed field.
