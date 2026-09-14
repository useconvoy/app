#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
portal_test_dir="$(mktemp -d)"
trap 'rm -rf "$portal_test_dir"' EXIT
./node_modules/.bin/tsc -p tsconfig.portal-tests.json --outDir "$portal_test_dir"
node --test "$portal_test_dir/tests/portal/portal.test.js"
