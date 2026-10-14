"""A module that calls the shelf collection through its generated client.

keys, get, list, subset and batch exist only in the engine's projection of the
collection, so the client has them only if it was generated from that.
"""

from dagger import function, object_type
from dagger_clients.shelf import shelf


@object_type
class ShelfCaller:
    @function
    async def report(self) -> str:
        items = shelf().items()
        keys = await items.keys()
        listed = [await item.name() for item in await items.list_()]
        got = await items.get("a").name()
        removed = await items.subset(["c"]).batch().removed()
        return f"keys={keys} list={listed} get={got} removed={removed}"
