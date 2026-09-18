#!/bin/sh
# Run the e2e checks on the local engine, the way engine-e2e runs them in the
# playground: a copy of this tree with the e2e workspace config in place of
# dagger.toml, so the fixture scopes are registered to the SDK.
#
#   hack/e2e-local.sh                  # every check
#   hack/e2e-local.sh find-client-root-check generate-scope-clients-check
#   E2E_WITH=other-branch hack/e2e-local.sh runtime-client-call-check
#   E2E_MODULE=.dagger/modules/engine-e2e hack/e2e-local.sh dev-client-call-check
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
# E2E_MODULE names the module whose checks run; it defaults to the e2e one.
#
# E2E_TIMEOUT stops a named check after that many seconds, 900 by default:
# on a shared engine a stuck connection otherwise holds a check until the
# engine drops it, some twenty minutes later.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
scratch=${E2E_SCRATCH:-$(mktemp -d)}
module=${E2E_MODULE:-.dagger/modules/e2e}
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
cat "$scratch/.dagger/modules/engine-e2e/workspace.toml" > "$scratch/dagger.toml"
cd "$scratch"
[ -d .git ] || git init --quiet
echo "checks of $module in $scratch" >&2
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
