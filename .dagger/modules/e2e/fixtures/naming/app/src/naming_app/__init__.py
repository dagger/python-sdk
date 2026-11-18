"""Declarations the engine names differently on either side of the cut.

HttpFetcher is NamingAppHttpFetcher by the legacy strcase rules and
NamingAppHTTPFetcher by the engine's identifier naming rules, and the Arch
member Arm64 is ARM_64 by the first and ARM64 by the second. A call through
the interface selects the type by its name, so the runtime has to use the
name the engine gave it.

The interface takes no Arch: the engine refuses an own interface whose
function takes an own enum in the module's own session, so the module would
fail to load its schema (core/interface.go, "cannot accept arg ... of
external type from dependency module").
"""

import enum
import typing

import dagger
from dagger import enum_type, function, interface, object_type


@enum_type
class Arch(enum.Enum):
    Arm64 = "arm64"


@interface
class HttpFetcher(typing.Protocol):
    @function
    async def get_url(self, path: str) -> str: ...


@object_type
class HttpClient:
    base_url: str = dagger.field()

    @function
    async def get_url(self, path: str) -> str:
        return f"{self.base_url}{path}"


@object_type
class NamingApp:
    @function
    def http_client(self, base_url: str) -> HttpClient:
        return HttpClient(base_url=base_url)

    @function
    async def fetch(self, fetcher: HttpFetcher, path: str, arch: Arch) -> str:
        """Call through the module's own interface, by the engine's names."""
        return f"{await fetcher.get_url(path)} {arch.value}"
