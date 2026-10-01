#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
configurations_test_dir="$(mktemp -d)"
trap 'rm -rf "$configurations_test_dir"' EXIT
./node_modules/.bin/tsc -p tsconfig.configurations-tests.json --outDir "$configurations_test_dir"
# The compiled client module imports React; resolve it from this package.
NODE_PATH="$(pwd)/node_modules" node --test "$configurations_test_dir"/tests/configurations/*.test.js
