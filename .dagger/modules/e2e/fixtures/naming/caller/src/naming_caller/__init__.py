"""Calls naming-app through its client, so its runtime calls its own interface."""

from dagger import check, function, object_type
from dagger_clients.naming_app import NamingAppArch, naming_app


@object_type
class NamingCaller:
    @function
    async def fetch(self) -> str:
        client = naming_app().http_client(base_url="https://example.com")
        # Whichever name the engine gave the member, the client has it.
        (arch,) = list(NamingAppArch)
        return await naming_app().fetch(fetcher=client, path="/x", arch=arch)

    @function
    @check
    async def fetch_check(self) -> None:
        got = await self.fetch()
        if got != "https://example.com/x arm64":
            msg = f"naming-app's call through its interface returned {got!r}"
            raise ValueError(msg)
