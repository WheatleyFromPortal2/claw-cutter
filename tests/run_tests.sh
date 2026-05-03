#!/usr/bin/env bash
# Run the full LionClaw test suite.
# Usage: ./tests/run_tests.sh [pytest args]
# Examples:
#   ./tests/run_tests.sh                          # run everything
#   ./tests/run_tests.sh tests/database/          # run one category
#   ./tests/run_tests.sh -k test_parse_cards      # run a specific test

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTEST="$REPO/.venv/bin/pytest"

if [[ ! -x "$PYTEST" ]]; then
  echo "ERROR: pytest not found at $PYTEST"
  echo "Run: $REPO/.venv/bin/pip install pytest"
  exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " LionClaw Test Suite"
echo " Repo: $REPO"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Run with colour, short tracebacks, section headers, and summary at the end.
"$PYTEST" \
  --rootdir="$REPO" \
  --color=yes \
  --tb=short \
  -v \
  --no-header \
  -p no:warnings \
  "$@"

EXIT=$?

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ $EXIT -eq 0 ]]; then
  echo " ALL TESTS PASSED ✓"
else
  echo " SOME TESTS FAILED — see output above for details"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exit $EXIT
