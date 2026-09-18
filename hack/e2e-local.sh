#!/bin/sh
# Run the e2e checks on the local engine, the way engine-e2e runs them in the
# playground: a copy of this tree with the e2e workspace config in place of
# dagger.toml, so the fixture scopes are registered to the SDK.
#
#   hack/e2e-local.sh                  # every check
#   hack/e2e-local.sh find-client-root-check generate-scope-clients-check
#
# E2E_SCRATCH names the copy; it defaults to a fresh temporary directory.
# Each named check logs to <scratch>.logs/<check>.log, next to the copy rather
# than in it, because the copy is synced with --delete; a failure prints the
# log's error lines.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
scratch=${E2E_SCRATCH:-$(mktemp -d)}
logs="$scratch.logs"
mkdir -p "$logs"
rsync -a --delete \
  --exclude .git --exclude .venv --exclude __pycache__ \
  --exclude .dagger/modules/e2e/out --exclude .dagger/modules/uc-probe \
  "$root/" "$scratch/"
cat "$scratch/.dagger/modules/engine-e2e/workspace.toml" > "$scratch/dagger.toml"
cd "$scratch"
[ -d .git ] || git init --quiet
echo "e2e checks in $scratch" >&2
if [ $# -eq 0 ]; then
  exec dagger check -m .dagger/modules/e2e
fi
status=0
for check in "$@"; do
  if dagger call -m .dagger/modules/e2e "$check" >"$logs/$check.log" 2>&1; then
    echo "PASS $check" >&2
  else
    echo "FAIL $check ($logs/$check.log)" >&2
    sed 's/\x1b\[[0-9;]*m//g' "$logs/$check.log" | grep -E '^ *! ' | awk '!seen[$0]++' | head -8 >&2
    status=1
  fi
done
exit $status
