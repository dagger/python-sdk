#!/bin/sh
# The floor job: the e2e checks on the engine of this SDK's floor release, the
# oldest the SDK claims to run on. An ordinary run (hack/e2e-local.sh) takes
# whatever engine it finds; this one provisions the floor's and asserts it
# answers before any check runs, so it cannot pass on another.
#
#   hack/e2e-floor.sh                          # every check
#   hack/e2e-floor.sh runtime-call-check       # named checks, as e2e-local.sh
#
# The floor is floorVersion in .dagger/modules/e2e/main.dang.
set -eu
root=$(CDPATH= cd "$(dirname "$0")/.." && /bin/pwd)
floor=$(sed -n 's/^ *let floorVersion: String! = "\(.*\)"$/\1/p' "$root/.dagger/modules/e2e/main.dang")
[ -n "$floor" ] || { echo "no floorVersion in .dagger/modules/e2e/main.dang" >&2; exit 1; }
export DAGGER_ENGINE="image://registry.dagger.io/engine:v$floor"
export E2E_ASSERT_FLOOR=1
echo "floor job on $DAGGER_ENGINE" >&2
exec "$root/hack/e2e-local.sh" "$@"
