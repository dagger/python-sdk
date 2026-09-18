from dagger_clients.core import core


async def hostname() -> str:
    return await core().container().from_("alpine:3.22").with_exec(["hostname"]).stdout()
