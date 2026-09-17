"""The hand-written SDK files depend on nothing generated.

Only ``dagger/__init__.py`` may name generated code.
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
    r"(?<![\w.])(dagger\.client\.gen|dagger_gen|dagger_clients)(?!\w)"
)

# Runs in its own interpreter: this one has the generated bindings loaded.
IMPORT_ALL = """
import importlib
import importlib.abc
import pathlib
import sys
import types

src = pathlib.Path(sys.argv[1])
blocked = ("dagger.client.gen", "dagger_gen", "dagger_clients")


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


@pytest.mark.parametrize("path", sdk_files(), ids=lambda p: str(p.relative_to(SRC)))
def test_sdk_file_names_no_generated_package(path: pathlib.Path):
    found = [
        f"{path.relative_to(SRC)}:{number}: {line.strip()}"
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if GENERATED_NAME_RE.search(line)
    ]

    assert not found, "\n".join(found)


def _is_submodule(name: str) -> bool:
    return (SRC / f"{name}.py").is_file() or (SRC / name).is_dir()


@pytest.mark.parametrize("path", sdk_files(), ids=lambda p: str(p.relative_to(SRC)))
def test_sdk_file_imports_no_name_from_the_package_init(path: pathlib.Path):
    """A name the init provides may be generated; a submodule never is."""
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names if a.name == "dagger"]
        elif isinstance(node, ast.ImportFrom) and node.module == "dagger":
            names = [a.name for a in node.names if not _is_submodule(a.name)]
        else:
            continue
        found += [f"{path.relative_to(SRC)}:{node.lineno}: {name}" for name in names]

    assert not found, "\n".join(found)
