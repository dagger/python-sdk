import os

from dagger import function, object_type
from greet import greeting


@object_type
class ResourcesApp:
    @function
    def greeting(self) -> str:
        return greeting()

    @function
    def call_digest(self) -> str:
        return os.environ["DAGGER_MODULE_CALL_DIGEST"]
