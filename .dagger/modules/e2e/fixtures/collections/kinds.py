"""Collections of every key type, copied into a module a check generates.

The keys are Int, Float, Boolean, an enum and a core scalar. Shelves returns
an item that is a collection itself. Items has a batch generator that
replaces its item's generator of the same name.
"""

import enum

from dagger import (
    collection,
    delta,
    enum_type,
    field,
    function,
    generate,
    get,
    keys,
    object_type,
)
from dagger_clients.core import Changeset, CollectionDelta, Platform, core


@object_type
class Entry:
    name: str = field()


@object_type
@collection
class Numbers:
    nums: list[int] = keys()
    sel: CollectionDelta | None = delta()

    @function
    @get
    def number(self, n: int) -> Entry:
        return Entry(name=f"n{n}")

    @function
    async def removed(self) -> list[str]:
        return await self.sel.removed_keys()


@object_type
@collection
class Ratios:
    vals: list[float] = keys()
    sel: CollectionDelta | None = delta()

    @function
    @get
    def ratio(self, v: float) -> Entry:
        return Entry(name=f"r{v}")

    @function
    async def removed(self) -> list[str]:
        return await self.sel.removed_keys()


@object_type
@collection
class Flags:
    vals: list[bool] = keys()
    sel: CollectionDelta | None = delta()

    @function
    @get
    def flag(self, v: bool) -> Entry:
        return Entry(name=f"f{v}")

    @function
    async def removed(self) -> list[str]:
        return await self.sel.removed_keys()


@enum_type
class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


@object_type
@collection
class Colors:
    vals: list[Color] = keys()
    sel: CollectionDelta | None = delta()

    @function
    @get
    def color(self, c: Color) -> Entry:
        return Entry(name=c.value)

    @function
    async def removed(self) -> list[str]:
        return await self.sel.removed_keys()


@object_type
@collection
class Platforms:
    vals: list[Platform] = keys()
    sel: CollectionDelta | None = delta()

    @function
    @get
    def platform(self, p: Platform) -> Entry:
        return Entry(name=str(p))

    @function
    async def removed(self) -> list[str]:
        return await self.sel.removed_keys()


@object_type
class Item:
    name: str = field()

    @function
    @generate
    def write(self) -> Changeset:
        msg = "replaced item generator ran: " + self.name
        raise ValueError(msg)


@object_type
@collection
class Items:
    names: list[str] = keys()

    @function
    @get
    def item(self, name: str) -> Item:
        return Item(name=name)

    @function
    @generate
    def write(self) -> Changeset:
        empty = core().directory()
        return empty.with_new_file("selected", ",".join(self.names)).changes(empty)

    @function
    def selected(self) -> list[str]:
        return self.names


@object_type
@collection
class Shelves:
    names: list[str] = keys()

    @function
    @get
    def shelf(self, name: str) -> Items:
        return Items(names=[name + "-1", name + "-2"])

    @function
    def count(self) -> int:
        return len(self.names)


@object_type
class Kinds:
    @function
    def numbers(self) -> Numbers:
        return Numbers(nums=[2, 1, 3])

    @function
    def ratios(self) -> Ratios:
        return Ratios(vals=[0.5, 1.5])

    @function
    def flags(self) -> Flags:
        return Flags(vals=[True, False])

    @function
    def colors(self) -> Colors:
        return Colors(vals=[Color.RED, Color.BLUE])

    @function
    def platforms(self) -> Platforms:
        return Platforms(vals=[Platform("linux/amd64"), Platform("linux/arm64")])

    @function
    def items(self) -> Items:
        return Items(names=["a", "b", "c"])

    @function
    def shelves(self) -> Shelves:
        return Shelves(names=["s1", "s2"])
