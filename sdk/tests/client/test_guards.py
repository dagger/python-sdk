import pytest

from dagger.client._guards import type_error


def test_type_error_names_the_calling_module():
    err = type_error("Container.with_exec", "args", "nope", "list[str]")

    assert str(err) == (
        f"Method {__name__}.Container.with_exec() parameter args='nope' "
        "expected to be of type list[str]."
    )


def test_generated_bindings_keep_their_message():
    from dagger.client.gen import dag

    with pytest.raises(TypeError, match=r"Method dagger\.client\.gen\.Container\."):
        dag.container().with_exec("nope")
