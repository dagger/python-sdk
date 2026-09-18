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
# and then calls demo, which calls lib through serveModule. It needs an engine
# that runs Dang entrypoints and has serveModule: v1.0.0-beta.14 or later.
#
# Why each module's manifest is rewritten: generation names the shared Dang
# entrypoint this repository publishes, dagger.io/sdk/python/entrypoint@v1,
# which runs the SDK of its release, not this checkout. The engine loads an
# entrypoint from a git ref or from a path inside the module, never from a path
# above it, so each module gets a copy of this checkout's entrypoint/ and its
# manifest names that copy. Every generation names the published one again,
# so the copy is named again after each. A user of a published SDK never does
# this.
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

# The shared entrypoint of this checkout, copied into .dagger/modules/<name>
# and named by the module's manifest.
entrypoint() {
  rm -rf ".dagger/modules/$1/checkout-entrypoint"
  mkdir -p ".dagger/modules/$1/checkout-entrypoint"
  cp python-sdk/entrypoint/*.dang ".dagger/modules/$1/checkout-entrypoint/"
  cat >".dagger/modules/$1/dagger-module.toml" <<TOML
name = "$1"

[entrypoint]
kind = "dang"
source = "./checkout-entrypoint"
TOML
}

say "dagger module init python --name lib"
dagger module init python --name lib -y
entrypoint lib
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

say "dagger module client add ../lib"
(cd .dagger/modules/demo && dagger module client add ../lib -y)
entrypoint demo

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

Every check of this SDK against your local engine, and on the floor release's:
  cd $sdk && hack/e2e-local.sh
  cd $sdk && hack/e2e-floor.sh
EOF
