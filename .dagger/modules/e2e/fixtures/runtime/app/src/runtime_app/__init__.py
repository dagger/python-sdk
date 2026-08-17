from dagger import function, object_type


@object_type
class RuntimeApp:
    @function
    def greeting(self) -> str:
        return "served by the python-sdk runtime"
