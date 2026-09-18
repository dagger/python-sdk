#!/bin/sh
# Try unified clients by hand, with the real CLI, in a throwaway workspace.
#
#   hack/try-unified-clients.sh            # a fresh temporary workspace
#   TRY_DIR=/tmp/uc hack/try-unified-clients.sh
#   TRY_SDK=/path/to/another/checkout hack/try-unified-clients.sh
#
# It builds this, from nothing:
#
#   <workspace>/dagger.toml                 declares this checkout as the python SDK
#   <workspace>/python-sdk/                  a copy of this checkout
#   <workspace>/.dagger/modules/lib/         a module with one function
#   <workspace>/.dagger/modules/demo/        a module that calls lib through a client
#
# and then calls demo, which calls lib. Nothing here needs the dev engine: on a
# released engine the client loads its module through the SDK's fallback path.
# The half that needs `serveModule`, and so an engine built from dagger/dagger
# at 284cd849, is the last command this script prints rather than runs.
#
# Why each module's manifest is rewritten: `[runtime] source = "python"` means
# the *builtin* Python SDK of the engine, not this checkout. A module that must
# run on this working tree names the runtime by path instead. A user of a
# published SDK never does this.
set -eu

# TRY_SDK runs the walkthrough on another checkout, a branch under review say.
sdk=${TRY_SDK:-$(CDPATH= cd "$(dirname "$0")/.." && /bin/pwd)}
dir=${TRY_DIR:-$(mktemp -d)}
say() { printf '\n== %s\n' "$1" >&2; }

say "workspace $dir"
rm -rf "$dir"
mkdir -p "$dir"
cd "$dir"
git init --quiet
rsync -a --exclude .git --exclude .venv --exclude __pycache__ "$sdk/" "$dir/python-sdk/"
cat >dagger.toml <<'TOML'
[modules.python-sdk]
source = "python-sdk"
check.skip = ["*"]

[sdks.python]
module = "python-sdk"
TOML

# The runtime of this checkout, from a module in .dagger/modules/<name>.
manifest() {
  cat >".dagger/modules/$1/dagger-module.toml" <<TOML
name = "$1"
engineVersion = "$(dagger version | awk '/^version:/ { print $2 }')"

[runtime]
source = "../../../python-sdk/runtime"
TOML
}

say "dagger module init python --name lib"
dagger module init python --name lib -y
manifest lib
cat >.dagger/modules/lib/src/lib/__init__.py <<'PY'
from dagger import function, object_type


@object_type
class Lib:
    @function
    def greeting(self, name: str = "world") -> str:
        return f"hello, {name}"
PY
dagger call -m lib greeting --name lib

say "dagger module init python --name demo"
dagger module init python --name demo -y
manifest demo

say "dagger module client add ../lib"
(cd .dagger/modules/demo && dagger module client add ../lib -y)

say "what the scope looks like now"
cat .dagger/modules/demo/pyproject.toml
find .dagger/modules/demo -maxdepth 3 -name pyproject.toml | sort
cat .dagger/modules/demo/clients/lib/src/dagger_clients/lib/_target.py

say "a module calling its client"
cat >.dagger/modules/demo/src/demo/__init__.py <<'PY'
from dagger import function, object_type
from dagger_clients.core import Container, core
from dagger_clients.lib import lib


@object_type
class Demo:
    @function
    async def hello(self, name: str = "unified clients") -> str:
        return await lib().greeting(name=name)

    @function
    def base(self) -> Container:
        return core().container().from_("alpine:3.21")
PY
dagger call -m demo hello --name "unified clients"
dagger call -m demo base with-exec --args=echo,core-still-works stdout

say "done: $dir"
cat >&2 <<EOF

The half above runs on a released engine. Two things need more:

1. Loading through the engine field, not the SDK's fallback:
     cd $sdk
     E2E_MODULE=.dagger/modules/engine-e2e hack/e2e-local.sh dev-client-call-check
   That builds an engine from dagger/dagger at 284cd849, which has serveModule.

2. Every check of this SDK against your local engine:
     cd $sdk && hack/e2e-local.sh
EOF
