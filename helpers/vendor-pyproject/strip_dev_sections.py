"""Strip a project file down to what a vendored library needs.

As published, the Python SDK's pyproject.toml declares the code generator as a
uv workspace member and a dev dependency. A module vendors the library without
the generator, and uv refuses to install a project whose workspace member is
missing, so those sections are dropped on the way in. The copy is a member of
the scope that holds it, and gets the marker every generated member carries.

With the temporary global client, the copy also carries the dagger_global
package, which imports core and the scope's clients, so it depends on them.

Usage: strip_dev_sections.py <in.toml> <out.toml> [<global client dependency>...]
"""

import sys

DROP = {"dependency-groups", "tool.uv.sources", "tool.uv.workspace"}
MARKER = '[tool.dagger]\ngenerated = "runtime"\n'
MODULE_NAME = 'module-name = "dagger"\n'
GLOBAL_MODULE_NAME = 'module-name = ["dagger", "dagger_global"]\n'
DEPENDENCIES = "dependencies = [\n"


def main(src: str, dst: str, global_dependencies: list[str]) -> None:
    kept: list[str] = []
    keep = True
    for line in open(src):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            keep = stripped.strip("[]") not in DROP
        if keep:
            kept.append(line)
    if global_dependencies:
        kept = with_global_client(kept, global_dependencies)
    open(dst, "w").write("".join(kept).rstrip() + "\n\n" + MARKER)


def with_global_client(lines: list[str], dependencies: list[str]) -> list[str]:
    # The SDK's own file is the only input, so a changed layout is a bug here,
    # not a user's file to be tolerant of.
    for expected in (MODULE_NAME, DEPENDENCIES):
        if lines.count(expected) != 1:
            msg = f"expected one line {expected.strip()!r} in the SDK's pyproject.toml"
            raise SystemExit(msg)
    out: list[str] = []
    for line in lines:
        if line == MODULE_NAME:
            out.append(GLOBAL_MODULE_NAME)
            continue
        out.append(line)
        if line == DEPENDENCIES:
            out.extend(f'    "{name}",\n' for name in dependencies)
    return out


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
