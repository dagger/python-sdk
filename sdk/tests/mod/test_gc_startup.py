import os
import subprocess
import sys

MODULE = """\
import dagger


@dagger.object_type
class Gcprobe:
    pass
"""


def _run(tmp_path, code: str, **env: str) -> list[str]:
    pkg = tmp_path / "gcprobe"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text(MODULE)
    env = {k: v for k, v in os.environ.items() if not k.startswith("DAGGER_")} | env
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), env.get("PYTHONPATH", "")])
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.split()


def test_module_runtime_imports_without_gc(tmp_path):
    code = (
        "import gc, dagger; before = gc.isenabled()\n"
        "from dagger.mod.cli import load_module; load_module()\n"
        "print(before, gc.isenabled(), gc.get_freeze_count() > 0)"
    )
    out = _run(
        tmp_path,
        code,
        DAGGER_MODULE="gcprobe",
        DAGGER_DEFAULT_PYTHON_PACKAGE="gcprobe",
        DAGGER_MAIN_OBJECT="Gcprobe",
    )
    assert out == ["False", "True", "True"]


def test_scripts_keep_gc(tmp_path):
    assert _run(tmp_path, "import gc, dagger; print(gc.isenabled())") == ["True"]
