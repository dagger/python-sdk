"""The hand-written SDK files depend on nothing generated.

Only ``dagger/__init__.py`` may name generated code: the optional import of
the temporary global client.
"""

import ast
import importlib.util
import pathlib
import re
import subprocess
import sys

import pytest

SRC = pathlib.Path(
    next(iter(importlib.util.find_spec("dagger").submodule_search_locations))
)
PACKAGE_INIT = SRC / "__init__.py"

GENERATED_NAME_RE = re.compile(
    r"(?<![\w.])(dagger\.client\.gen|dagger_gen|dagger_clients|dagger_global)(?!\w)"
)

# Runs in its own interpreter: this one has the generated bindings loaded.
IMPORT_ALL = """
import importlib
import importlib.abc
import pathlib
import sys
import types

src = pathlib.Path(sys.argv[1])
blocked = ("dagger.client.gen", "dagger_gen", "dagger_clients", "dagger_global")


class GeneratedCodeImportedError(Exception):
    pass


class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if any(name == b or name.startswith(b + ".") for b in blocked):
            # Not an ImportError: a suppressed import must not hide it.
            raise GeneratedCodeImportedError(name)


sys.meta_path.insert(0, Blocker())

# The package init is the one file that may load generated code, so a bare
# package stands in for it.
package = types.ModuleType("dagger")
package.__path__ = [str(src)]
sys.modules["dagger"] = package

for path in sorted(src.rglob("*.py")):
    if path in (src / "__init__.py", src / "client" / "gen.py"):
        continue
    parts = path.relative_to(src).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    name = ".".join(("dagger", *parts))
    importlib.import_module(name)
    print(name)
"""


def sdk_files() -> list[pathlib.Path]:
    return sorted(p for p in SRC.rglob("*.py") if p != PACKAGE_INIT)


def test_sdk_files_import_without_generated_code():
    proc = subprocess.run(
        [sys.executable, "-c", IMPORT_ALL, str(SRC)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    imported = proc.stdout.split()
    assert "dagger.mod._module" in imported
    assert "dagger.provisioning._engine" in imported
    assert len(imported) == len(sdk_files()) - 1


# The rule is that the SDK imports nothing generated, and the AST scan below
# enforces it. A help message naming the package a user has to import is not
# a dependency, so this one string is let through; every other name in the
# text still fails.
HELP_TEXT = {
    pathlib.Path("client/_session.py"): "(from dagger_clients.core import core)",
}


@pytest.mark.parametrize("path", sdk_files(), ids=lambda p: str(p.relative_to(SRC)))
def test_sdk_file_text_names_no_generated_package(path: pathlib.Path):
    """Catches a name in a string, which no import resolution sees."""
    allowed = HELP_TEXT.get(path.relative_to(SRC))
    found = [
        f"{path.relative_to(SRC)}:{number}: {line.strip()}"
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if GENERATED_NAME_RE.search(line.replace(allowed, "") if allowed else line)
    ]

    assert not found, "\n".join(found)


def _is_submodule(name: str) -> bool:
    # Listed, not stat'ed: a case-insensitive filesystem matches Client to client/.
    return name in {p.stem for p in SRC.iterdir()}


GENERATED = ("dagger.client.gen", "dagger_gen", "dagger_clients", "dagger_global")


def _is_generated(name: str) -> bool:
    return any(name == g or name.startswith(g + ".") for g in GENERATED)


def _generated_imports(source: str, module: str) -> list[tuple[int, str]]:
    """Every import that lands on generated code, wherever it sits in the file.

    Names are resolved to absolute before judging, so ``from .gen import X``
    counts the same as ``from dagger.client.gen import X``.
    """
    package = module if _is_package(module) else module.rpartition(".")[0]
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            bad = [
                f"import {a.name}"
                for a in node.names
                if _is_generated(a.name) or a.name == "dagger"
            ]
        elif isinstance(node, ast.ImportFrom):
            base = importlib.util.resolve_name(
                "." * node.level + (node.module or ""), package
            )
            bad = [
                f"from {base} import {a.name}"
                for a in node.names
                if _is_generated(base)
                or _is_generated(f"{base}.{a.name}")
                # A name the init provides may be generated; a submodule never is.
                or (base == "dagger" and not _is_submodule(a.name))
            ]
        elif isinstance(node, ast.Call) and (name := _literal_import(node, package)):
            bad = [f"{_call_name(node)}({name!r})"] if _is_generated(name) else []
        else:
            continue
        found += [(node.lineno, stmt) for stmt in bad]
    return found


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _literal_import(call: ast.Call, package: str) -> str | None:
    """The absolute target of ``import_module``/``__import__`` on a literal name.

    A computed name can't be judged statically, so the rule stops at "no
    static or literal-dynamic import of generated code"; the subprocess
    import test is what catches the rest.
    """
    if _call_name(call) not in ("import_module", "__import__") or not call.args:
        return None
    name = call.args[0]
    if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
        return None
    anchor = next(
        (k.value for k in call.keywords if k.arg == "package"),
        call.args[1] if len(call.args) > 1 else None,
    )
    if isinstance(anchor, ast.Constant) and isinstance(anchor.value, str):
        package = anchor.value
    return importlib.util.resolve_name(name.value, package)


def _is_package(module: str) -> bool:
    return (SRC.parent / module.replace(".", "/")).is_dir()


def _module_name(path: pathlib.Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("dagger", *parts))


@pytest.mark.parametrize("path", sdk_files(), ids=lambda p: str(p.relative_to(SRC)))
def test_sdk_file_imports_no_generated_package(path: pathlib.Path):
    found = _generated_imports(path.read_text(), _module_name(path))

    assert not found, "\n".join(
        f"{path.relative_to(SRC)}:{line}: {stmt}" for line, stmt in found
    )


LAZY_MUTATION = """
def root_type():
    from .gen import Client

    return Client
"""

DYNAMIC_MUTATION = """
def f():
    from importlib import import_module
    return import_module(".gen", __package__)
"""


@pytest.mark.parametrize(
    ("source", "module"),
    [
        pytest.param(LAZY_MUTATION, "dagger.client.base", id="lazy-relative"),
        pytest.param(DYNAMIC_MUTATION, "dagger.client.base", id="dynamic-relative"),
        pytest.param(
            "import importlib\nimportlib.import_module('dagger_gen')\n",
            "dagger.log",
            id="dynamic-attribute",
        ),
        pytest.param(
            "import_module('.gen', package='dagger.client')\n",
            "dagger.log",
            id="dynamic-package-kwarg",
        ),
        pytest.param(
            "__import__('dagger.client.gen')\n", "dagger.log", id="dunder-import"
        ),
        pytest.param("from . import gen\n", "dagger.client.base", id="from-dot"),
        pytest.param("from .. import gen\n", "dagger.client.sub.x", id="from-dotdot"),
        pytest.param(
            "from ..client import gen\n",
            "dagger.provisioning._engine",
            id="sibling-package",
        ),
        pytest.param(
            "from dagger.client import gen\n", "dagger.mod._module", id="from-parent"
        ),
        pytest.param(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from dagger.client.gen import Client\n",
            "dagger.mod._module",
            id="type-checking",
        ),
        pytest.param(
            "class A:\n    def m(self):\n        import dagger.client.gen\n",
            "dagger.mod._module",
            id="method-body",
        ),
        pytest.param("import dagger_gen\n", "dagger.log", id="dagger_gen"),
        pytest.param("import dagger_global\n", "dagger.log", id="dagger_global"),
        pytest.param(
            "from dagger_clients.core import core\n", "dagger.log", id="dagger_clients"
        ),
        pytest.param("def f():\n    import dagger\n", "dagger.log", id="lazy-package"),
        pytest.param("from . import Client\n", "dagger.log", id="dot-is-the-init"),
        pytest.param(
            "from ... import Client\n", "dagger.client.sub.x", id="dots-to-the-init"
        ),
    ],
)
def test_guard_rejects_generated_import(source: str, module: str):
    assert _generated_imports(source, module)


@pytest.mark.parametrize(
    ("source", "module"),
    [
        pytest.param(
            "from ._core import Context\n", "dagger.client.base", id="sibling"
        ),
        pytest.param("from . import _core\n", "dagger.client.base", id="from-dot"),
        pytest.param("from dagger import mod\n", "dagger.log", id="submodule"),
        pytest.param(
            "from dagger.client.base import Root\n", "dagger.mod._module", id="absolute"
        ),
        pytest.param("import dagger_gen_tools\n", "dagger.log", id="prefix-only"),
        pytest.param("import gen\n", "dagger.client.base", id="third-party-gen"),
        pytest.param(
            "import_module(name)\n", "dagger.client.base", id="computed-dynamic"
        ),
        pytest.param(
            "import_module('.' + name, __package__)\n",
            "dagger.client.base",
            id="computed-relative",
        ),
        pytest.param(
            "import_module('._core', __package__)\n",
            "dagger.client.base",
            id="dynamic-sibling",
        ),
    ],
)
def test_guard_accepts_sdk_import(source: str, module: str):
    assert not _generated_imports(source, module)


def test_package_init_names_only_the_global_client():
    """The init's generated imports are the optional global client's.

    One for type checkers, and the lazy one at run time.
    """
    found = [stmt for _, stmt in _generated_imports(PACKAGE_INIT.read_text(), "dagger")]

    assert found == ["from dagger_global import *", "import_module('dagger_global')"]
