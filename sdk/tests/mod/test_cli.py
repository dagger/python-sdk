import sys
import textwrap

import pytest

from dagger.mod import cli
from dagger.mod._exceptions import ModuleLoadError
from dagger.mod._utils import get_parent_module_doc

SCRIPT = textwrap.dedent(
    '''\
    # /// script
    # dependencies = ["dagger-io"]
    # ///
    """A script module, whose docstring describes no package."""

    from dagger.mod import Module

    mod = Module(main_name="App")


    @mod.object_type
    class App:
        @mod.function
        def hello(self) -> str:
            return "hello"
    '''
)


@pytest.fixture
def script(tmp_path, monkeypatch):
    def _write(contents: str) -> None:
        path = tmp_path / "main.py"
        path.write_text(contents)
        monkeypatch.setenv("DAGGER_MODULE_SCRIPT", str(path))

    monkeypatch.setattr(cli, "MAIN_OBJECT", "App")
    monkeypatch.delitem(sys.modules, cli.SCRIPT_MODULE, raising=False)
    yield _write
    sys.modules.pop(cli.SCRIPT_MODULE, None)


def test_script_loads_as_the_top_level_module(script):
    script(SCRIPT)
    mod = cli.load_module()
    app = sys.modules[cli.SCRIPT_MODULE].App
    assert mod is app.__dagger_module__
    assert app.__module__ == cli.SCRIPT_MODULE
    assert get_parent_module_doc(app) is None
    assert mod.describe().description is None


def test_script_wins_over_the_default_package(script, monkeypatch):
    monkeypatch.setattr(cli, "IMPORT_PKG", "missing_package")
    script(SCRIPT)
    assert cli.get_entry_point().value == f"{cli.SCRIPT_MODULE}:App"


def test_script_that_fails_to_import(script):
    script("raise RuntimeError('broken script')\n")
    with pytest.raises(ModuleLoadError, match="broken script"):
        cli.load_module()
    assert cli.SCRIPT_MODULE not in sys.modules
