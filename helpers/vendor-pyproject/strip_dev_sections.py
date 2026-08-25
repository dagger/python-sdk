"""Strip a project file down to what a vendored library needs.

As published, the Python SDK's pyproject.toml declares the code generator as a
uv workspace member and a dev dependency. A module vendors the library without
the generator, and uv refuses to install a project whose workspace member is
missing, so those sections are dropped on the way in.

Usage: strip_dev_sections.py <in.toml> <out.toml>
"""

import sys

DROP = {"dependency-groups", "tool.uv.sources", "tool.uv.workspace"}


def main(src: str, dst: str) -> None:
    kept: list[str] = []
    keep = True
    for line in open(src):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            keep = stripped.strip("[]") not in DROP
        if keep:
            kept.append(line)
    open(dst, "w").write("".join(kept).rstrip() + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
