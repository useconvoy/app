#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
platform_test_dir="$(mktemp -d)"
trap 'rm -rf "$platform_test_dir"' EXIT
./node_modules/.bin/tsc -p tsconfig.platform-tests.json --outDir "$platform_test_dir"
node --test "$platform_test_dir/tests/platform/proxy.test.js"
