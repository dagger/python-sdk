from dagger import function, object_type


@object_type
class TomlApp:
    @function
    def greeting(self) -> str:
        return "hello from the toml fixture"
