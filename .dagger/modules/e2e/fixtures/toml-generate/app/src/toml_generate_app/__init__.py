from dagger import function, object_type


@object_type
class TomlGenerateApp:
    @function
    def hello(self) -> str:
        return "hello"
