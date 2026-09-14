#!/usr/bin/env bash
# Convoy: validate an EXISTING llama.cpp build tree's identity before build-llama-cpp.sh --reuse-build
# runs a validated incremental build in it. Pure inspection: nothing here configures, builds or writes.
#
# Usage: reuse-build-check.sh --build DIR --src DIR --nvcc PATH --nvcc-version V -- -DFLAG=VALUE ...
#   --nvcc-version is the FULL host nvcc version (e.g. 12.6.68, from `nvcc --version` "V12.6.68")
# Checks (all must hold; each is printed; on success the last lines are key=value facts for the caller):
#   1. CMakeCache.txt exists and CMAKE_HOME_DIRECTORY is the pinned source tree
#   2. every recipe -D entry is present in the cache with exactly the recipe's value
#   3. the compilers CMake recorded are the host's: CMAKE_CUDA_COMPILER path == the host nvcc, and the
#      versions CMake detected (CMakeFiles/<cmake-version>/CMakeCUDACompiler.cmake and
#      CMakeCXXCompiler.cmake: set(CMAKE_<LANG>_COMPILER_VERSION "...")) equal the host nvcc's full
#      version and what the recorded C++ compiler path itself reports (-dumpfullversion)
#   4. bin/llama-server exists and is executable
set -euo pipefail
BUILD=""; SRC=""; NVCC=""; NVCC_VERSION=""
FLAGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build) BUILD="$2"; shift 2 ;;
    --src) SRC="$2"; shift 2 ;;
    --nvcc) NVCC="$2"; shift 2 ;;
    --nvcc-version) NVCC_VERSION="$2"; shift 2 ;;
    --) shift; FLAGS=("$@"); break ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$BUILD" && -n "$SRC" && -n "$NVCC" && -n "$NVCC_VERSION" && ${#FLAGS[@]} -gt 0 ]] \
  || { echo "need --build --src --nvcc --nvcc-version and -- flags" >&2; exit 2; }
ok()   { printf '   [verified] %s\n' "$*"; }
fail() { printf '   [FAILED] %s\n' "$*" >&2; exit 1; }
CACHE="$BUILD/CMakeCache.txt"
cache_value() { sed -nE "s/^$1:[A-Z]+=(.*)$/\1/p" "$CACHE" | head -n1; }
detected_version() {  # $1 = LANG (CUDA|CXX|C): the version CMake detected, from CMakeFiles/<ver>/CMake<LANG>Compiler.cmake
  local f
  f="$(find "$BUILD/CMakeFiles" -mindepth 2 -maxdepth 2 -name "CMake$1Compiler.cmake" 2>/dev/null | sort | tail -n1 || true)"
  [[ -n "$f" && -r "$f" ]] || return 1
  sed -nE "s/^set\(CMAKE_$1_COMPILER_VERSION \"([^\"]+)\"\)$/\1/p" "$f" | head -n1
}

# 1. cache + source identity
[[ -r "$CACHE" ]] || fail "$CACHE missing; nothing to reuse"
CACHE_SRC="$(cache_value CMAKE_HOME_DIRECTORY)"
[[ -n "$CACHE_SRC" && "$(readlink -f "$CACHE_SRC")" == "$(readlink -f "$SRC")" ]] || fail "build tree was configured from '${CACHE_SRC:-unset}', not the pinned source $SRC"
ok "cache configured from the pinned source tree"

# 2. recipe cache entries
for f in "${FLAGS[@]}"; do
  name="${f#-D}"; name="${name%%=*}"; want="${f#*=}"
  got="$(cache_value "$name")"
  [[ "$got" == "$want" ]] || fail "CMake cache $name='${got:-unset}' differs from the recipe's '$want'; this tree is not the recipe's build"
done
ok "CMake cache carries exactly the recipe's ${#FLAGS[@]} entries"

# 3. toolchain identity as CMake recorded it (paths in the cache, versions in the detection files)
CACHE_NVCC="$(cache_value CMAKE_CUDA_COMPILER)"
[[ -n "$CACHE_NVCC" && "$(readlink -f "$CACHE_NVCC")" == "$(readlink -f "$NVCC")" ]] || fail "cache CUDA compiler '${CACHE_NVCC:-unset}' is not the host nvcc $NVCC"
DET_CUDA="$(detected_version CUDA || true)"
[[ -n "$DET_CUDA" ]] || fail "CMakeFiles/<version>/CMakeCUDACompiler.cmake has no CMAKE_CUDA_COMPILER_VERSION"
[[ "$DET_CUDA" == "$NVCC_VERSION" ]] || fail "CMake detected nvcc $DET_CUDA, the host nvcc is $NVCC_VERSION"
CACHE_CXX="$(cache_value CMAKE_CXX_COMPILER)"
[[ -n "$CACHE_CXX" && -x "$CACHE_CXX" ]] || fail "cache C++ compiler '${CACHE_CXX:-unset}' does not exist on this host"
DET_CXX="$(detected_version CXX || true)"
[[ -n "$DET_CXX" ]] || fail "CMakeFiles/<version>/CMakeCXXCompiler.cmake has no CMAKE_CXX_COMPILER_VERSION"
HOST_CXX="$("$CACHE_CXX" -dumpfullversion 2>/dev/null || "$CACHE_CXX" -dumpversion 2>/dev/null || echo unknown)"
[[ "$HOST_CXX" == "$DET_CXX" ]] || fail "C++ compiler $CACHE_CXX reports $HOST_CXX now, CMake detected $DET_CXX when this tree was configured"
ok "toolchain identity: nvcc $DET_CUDA at $CACHE_NVCC, C++ $DET_CXX at $CACHE_CXX (both equal the host's)"

# 4. artifact present
[[ -x "$BUILD/bin/llama-server" ]] || fail "$BUILD/bin/llama-server is missing or not executable; the compile did not finish"
ok "bin/llama-server present"
echo "reuse-build-check: ok"
echo "cxx_compiler=$CACHE_CXX"
echo "cxx_version=$DET_CXX"
echo "cuda_compiler=$CACHE_NVCC"
echo "cuda_compiler_version=$DET_CUDA"
