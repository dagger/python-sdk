#!/bin/sh
# Run the e2e checks on the local engine, in a copy of this tree, so a check
# never writes into it. dagger.toml is the e2e workspace config: it registers
# this checkout as the python SDK and the fixture scopes to it.
#
#   hack/e2e-local.sh                  # every check
#   hack/e2e-local.sh find-client-root-check generate-scope-clients-check
#   E2E_WITH=other-branch hack/e2e-local.sh runtime-client-call-check
#
# It runs on whatever engine the CLI finds. hack/e2e-floor.sh runs the same
# checks on the floor release's engine, and asserts it.
#
# E2E_SCRATCH names the copy; it defaults to a fresh temporary directory.
# Each named check logs to <scratch>.logs/<check>.log, next to the copy rather
# than in it, because the copy is synced with --delete; a failure prints the
# log's error lines.
#
# E2E_WITH names a branch that must land with this one: its changes since the
# two forked are applied to the copy, so the checks run on both together
# without a merge in either branch.
#
# E2E_TIMEOUT stops a named check after that many seconds, 900 by default:
# on a shared engine a stuck connection otherwise holds a check until the
# engine drops it, some twenty minutes later.
set -eu
root=$(CDPATH= cd "$(dirname "$0")/.." && /bin/pwd)
scratch=${E2E_SCRATCH:-$(mktemp -d)}
module=.dagger/modules/e2e
logs="$scratch.logs"
mkdir -p "$logs"
rsync -a --delete \
  --exclude .git --exclude .venv --exclude __pycache__ \
  --exclude .dagger/modules/e2e/out \
  "$root/" "$scratch/"
if [ -n "${E2E_WITH:-}" ]; then
  base=$(git -C "$root" merge-base HEAD "$E2E_WITH")
  git -C "$root" diff --binary "$base" "$E2E_WITH" | git -C "$scratch" apply -
  echo "applied $E2E_WITH since $(git -C "$root" rev-parse --short "$base")" >&2
fi
CDPATH= cd "$scratch"
[ -d .git ] || git init --quiet
echo "checks of $module in $scratch" >&2
# Set by hack/e2e-floor.sh: the engine must be the floor before any check.
if [ -n "${E2E_ASSERT_FLOOR:-}" ]; then
  dagger call -m "$module" assert-floor-engine >"$logs/assert-floor-engine.log" 2>&1 || {
    echo "FAIL the engine is not the floor ($logs/assert-floor-engine.log)" >&2
    sed 's/\x1b\[[0-9;]*m//g' "$logs/assert-floor-engine.log" | grep -E '^ *! ' | awk '!seen[$0]++' | head -4 >&2
    exit 1
  }
  echo "engine is the floor" >&2
fi
if [ $# -eq 0 ]; then
  exec dagger check -m "$module"
fi
status=0
for check in "$@"; do
  dagger call -m "$module" "$check" >"$logs/$check.log" 2>&1 &
  call=$!
  (
    trap 'kill "$nap" 2>/dev/null; exit 0' TERM
    sleep "${E2E_TIMEOUT:-900}" &
    nap=$!
    wait "$nap"
    kill "$call" 2>/dev/null && echo "TIMEOUT after ${E2E_TIMEOUT:-900}s" >>"$logs/$check.log"
  ) &
  watchdog=$!
  if wait "$call"; then
    echo "PASS $check" >&2
  else
    echo "FAIL $check ($logs/$check.log)" >&2
    sed 's/\x1b\[[0-9;]*m//g' "$logs/$check.log" | grep -E '^ *! |^TIMEOUT' | awk '!seen[$0]++' | head -8 >&2
    status=1
  fi
  kill "$watchdog" 2>/dev/null || true
done
exit $status
