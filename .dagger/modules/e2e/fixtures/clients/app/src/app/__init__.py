from dagger import function, object_type
from dagger.clients.app import app
from dagger.clients.dep import dep
from dagger.clients.greeter import greeter


@object_type
class App:
    @function
    async def greet_via_dep(self) -> str:
        return await dep().greet("dep")

    @function
    async def greet_via_alias(self) -> str:
        return await greeter().greet("alias")

    @function
    def local(self) -> str:
        return "local"

    @function
    async def greet_self(self) -> str:
        return (await app().local()).upper()
