import dagger
from dagger import dag, function, object_type


@object_type
class Dep:
    @function
    def greet(self, name: str) -> str:
        return f"hello, {name}"

    @function
    def container(self) -> dagger.Container:
        return dag.container().from_("alpine:3.22")
