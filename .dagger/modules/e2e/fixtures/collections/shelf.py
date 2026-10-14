"""A collection, copied into a module the collections check generates.

The members carry names of their own, so the engine finds them by the
markers, not by the names it falls back to.
"""

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
    prefix: str = ""

    @function
    @get
    def lookup(self, name: str) -> Item:
        return Item(name=self.prefix + name)

    @function
    def selected(self) -> list[str]:
        return self.names

    @function
    async def added(self) -> list[str]:
        return await self.selection.added_keys()

    @function
    async def removed(self) -> list[str]:
        return await self.selection.removed_keys()

    @function
    def change(self, names: list[str]) -> "Items":
        return dataclasses.replace(self, names=names)

    @function
    def fresh(self) -> "Items":
        return Items(names=["z"], prefix=self.prefix)


@object_type
class Shelf:
    @function
    def items(self) -> Items:
        return Items(names=["b", "a", "c"], prefix="item:")
