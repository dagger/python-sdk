"""Take the empty module out of the schema core is read through.

A client-facing schema is only served for a module, so core is read through
core-schema, a module with nothing in it. The code generator leaves that
module out of core, but it would take it for a client of its own in the
temporary global client. So its types and fields go, and with them, in the
views before v1.0.0, the ID scalar and the loader the engine names after its
type without saying which module they belong to.

Usage: core_only.py <in.json> <out.json>
"""

import json
import sys

MODULE = "core-schema"


def main(src: str, dst: str) -> None:
    result = json.load(open(src))
    schema = result["__schema"]
    gone = {t["name"] for t in schema["types"] if is_stand_in(t)}
    if not gone:
        msg = f"no type of the module {MODULE!r} in {src}"
        raise SystemExit(msg)
    gone |= {name + "ID" for name in gone}
    loaders = {f"load{name}FromID" for name in gone}
    schema["types"] = [t for t in schema["types"] if t["name"] not in gone]
    for t in schema["types"]:
        if t.get("fields") is not None:
            t["fields"] = [
                f
                for f in t["fields"]
                if not is_stand_in(f) and f["name"] not in loaders
            ]
        for key in ("possibleTypes", "interfaces"):
            if t.get(key) is not None:
                t[key] = [ref for ref in t[key] if ref["name"] not in gone]
    json.dump(result, open(dst, "w"))


def is_stand_in(node: dict) -> bool:
    return any(
        d["name"] == "sourceMap"
        and any(
            a["name"] == "module" and json.loads(a["value"]) == MODULE
            for a in d.get("args") or []
        )
        for d in node.get("directives") or []
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
