#!/usr/bin/env bash
# Convoy: native llama.cpp CUDA build + runtime package on a Jetson Orin Nano (JetPack 6.2.x, one named track).
#
# STATUS: pending device verification. This script was authored and shellcheck-linted without a Jetson,
# without CUDA and without network access to NVIDIA hosts; it has NOT been executed on hardware. Every
# check it performs is printed so the first hardware run leaves an auditable trail.
#
# What it does (docs/JETSON_GUIDE.md "Build and register the runtime"):
#   1. verify the host matches the selected track's platform tuple (aarch64, L4T <track>, CUDA 12.6); a
#      mismatch FAILS unless --allow-tuple-mismatch is given, which is also recorded in the receipt
#   2. clone llama.cpp and check out the pinned commit (tag v0.4.0), verify HEAD == commit
#   3. configure a CLEAN build directory with the recipe's CMake flags (DEFAULT_CMAKE in
#      server/convoy_server/services/catalog.py; CMAKE_CUDA_ARCHITECTURES is passed typed :STRING so
#      CMake caches it as a STRING and not UNINITIALIZED) and build the llama-server target
#   4. package bin/llama-server plus the complete closure of non-system shared libraries from the build
#      tree (ldd + every *.so* the build produced), materialising SONAME names as regular files (the
#      agent's extractor rejects symlinks); CUDA/driver/system libraries are NOT bundled but listed
#   5. verify the package starts OUTSIDE the build tree (build tree hidden while testing so RPATH/RUNPATH
#      cannot resolve anything), `--version` mentions the commit, `--help` runs
#   6. hash every file, write receipt.json (server ReceiptIn schema) and runtime-<commit>-<arch>.tar.gz
#      with canonical member paths, then re-inspect the archive the way the server does
#
# Usage:  scripts/jetson/build-llama-cpp.sh [--track jp623|l4t3647] [--work DIR] [--jobs N] [--skip-clone]
#                                          [--reuse-build] [--allow-tuple-mismatch] [--print-track]
# --reuse-build: validated INCREMENTAL build in an existing tree. Never configures or cleans. reuse-build-
#   check.sh inspects the tree first (pinned clean source, EXACTLY the recipe's cache entries, the toolchain
#   CMake recorded == the host's full nvcc version and the recorded C++ compiler's own version, llama-server
#   present); then `cmake --build` is EXECUTED (an up-to-date tree does nothing; an unfinished one finishes)
#   and recorded truthfully (verification.incremental_build_steps), the identity is revalidated, and the same
#   packaging, out-of-tree startup checks, hashing and receipt follow. The receipt says clean_build_dir=false
#   and verification.build_tree="reused: ...". Use it once the original build process has exited.
# Env:    CONVOY_BUILD_ROOT (default ~/convoy-build), CONVOY_BUILD_JOBS (default 4: the Orin Nano has
#         8 GB unified memory and CUDA kernels compile large; use zram/swap and MAXN power mode),
#         CONVOY_BUILD_TRACK (default jp623; see the track table below)
# The script never changes the power mode, flashes or updates the OS: it only reads the host tuple.
set -euo pipefail

# ---------------------------------------------------------------- pins (must match the recipe) ----
LLAMA_REPO="https://github.com/ggml-org/llama.cpp"
LLAMA_COMMIT="5266f24da75dc449bd56cbed7addb9c8e4a6a73e"
LLAMA_TAG="v0.4.0"
# Recipe identity: exactly DEFAULT_CMAKE from server/convoy_server/services/catalog.py, same order.
# These strings go into the receipt as `cmake_flags`; the actual cmake invocation (below) types the
# CUDA architectures entry so the cache records it as STRING (value identical).
CMAKE_FLAGS=(
  -DGGML_CUDA=ON
  -DCMAKE_CUDA_ARCHITECTURES=87
  -DCMAKE_BUILD_TYPE=Release
  -DLLAMA_BUILD_TESTS=OFF
  -DLLAMA_BUILD_EXAMPLES=OFF
  -DLLAMA_BUILD_SERVER=ON
  -DLLAMA_CURL=OFF
  -DGGML_NATIVE=OFF
  -DLLAMA_USE_PREBUILT_UI=OFF
  -DLLAMA_BUILD_UI=OFF
)
# Recipe target tuple = one named CUDA track (CUDA_TRACKS in catalog.py). Same commit, flags, SM 8.7 and
# CUDA 12.6 on both; they differ in the L4T release the receipt must prove:
#   jp623    JetPack 6.2.3 = L4T 36.5.2 / CUDA 12.6 (the original supervisor-verified pin; default)
#   l4t3647  L4T 36.4.7 / CUDA 12.6 as observed on the project's Orin Nano Developer Kit Super
#            (2026-09-13). Named by the observed L4T release: no JetPack version is established for it
#            (no nvidia-jetpack metapackage; NVIDIA lists JetPack 6.2.2 with Jetson Linux 36.5), so the
#            receipt records jetpack=null. Candidate track, physical qualification pending.
# Select with --track or CONVOY_BUILD_TRACK. The receipt records the track and target_expected, and the
# artifact must be registered under a recipe created for the SAME track (register-runtime.sh and the
# server compare the observed tuple with the recipe's). --allow-tuple-mismatch is NOT a track selector.
EXPECTED_ARCH="aarch64"
EXPECTED_CUDA="12.6"
EXPECTED_CC="8.7"
TRACK="${CONVOY_BUILD_TRACK:-jp623}"
set_track() {
  case "$1" in
    jp623) EXPECTED_L4T="36.5.2"; EXPECTED_JETPACK="6.2.3" ;;
    l4t3647) EXPECTED_L4T="36.4.7"; EXPECTED_JETPACK="" ;;  # empty = not established (receipt: null)
    *) echo "unknown track '$1' (known: jp623, l4t3647)" >&2; exit 2 ;;
  esac
}

WORK="${CONVOY_BUILD_ROOT:-$HOME/convoy-build}"
JOBS="${CONVOY_BUILD_JOBS:-4}"
SKIP_CLONE=0
REUSE_BUILD=0
ALLOW_MISMATCH=0
PRINT_TRACK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --work) WORK="$2"; shift 2 ;;
    --jobs) JOBS="$2"; shift 2 ;;
    --track) TRACK="$2"; shift 2 ;;
    --print-track) PRINT_TRACK=1; shift ;;
    --skip-clone) SKIP_CLONE=1; shift ;;
    --reuse-build) REUSE_BUILD=1; SKIP_CLONE=1; shift ;;
    --allow-tuple-mismatch) ALLOW_MISMATCH=1; shift ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
set_track "$TRACK"
if [[ "$PRINT_TRACK" -eq 1 ]]; then
  # machine-readable, no host checks: what this invocation would require and record
  printf 'track=%s\narch=%s\nl4t=%s\ncuda=%s\njetpack=%s\ncompute_capability=%s\ncommit=%s\ntag=%s\n' \
    "$TRACK" "$EXPECTED_ARCH" "$EXPECTED_L4T" "$EXPECTED_CUDA" "${EXPECTED_JETPACK:-not-established}" "$EXPECTED_CC" "$LLAMA_COMMIT" "$LLAMA_TAG"
  printf 'cmake_flags=%s\n' "${CMAKE_FLAGS[*]}"
  exit 0
fi

SRC="$WORK/llama.cpp"
BUILD="$WORK/build"
PKG="$WORK/pkg"
OUT="$WORK/out"
VERIFY="$WORK/verify"
SYSTEM_LIB_PREFIXES=(/lib /lib64 /usr/lib /usr/lib64 /usr/local/cuda /usr/local/cuda-12.6 /usr/local/cuda-12)

log()  { printf '\n==> %s\n' "$*"; }
ok()   { printf '   [verified] %s\n' "$*"; }
fail() { printf '   [FAILED] %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing tool: $1 ($2)"; }

# ---------------------------------------------------------------- 0. host checks ------------------
log "host checks"
ARCH="$(uname -m)"
[[ "$ARCH" == "$EXPECTED_ARCH" ]] || fail "this build must run on the Jetson itself ($EXPECTED_ARCH), got $ARCH"
ok "arch $ARCH"
[[ -r /etc/nv_tegra_release ]] || fail "/etc/nv_tegra_release missing: not an L4T (JetPack) host"
L4T_LINE="$(head -n1 /etc/nv_tegra_release)"
# Same parse as agent/convoy_agent/hardware.py: "R36 (release), REVISION: 5.2" -> 36.5.2
L4T_OBS="$(printf '%s' "$L4T_LINE" | sed -nE 's/.*R([0-9]+)[[:space:]]*\(release\),[[:space:]]*REVISION:[[:space:]]*([0-9.]+).*/\1.\2/p')"
echo "   L4T: $L4T_LINE  (parsed: ${L4T_OBS:-unparsed})"
export PATH="/usr/local/cuda/bin:$PATH"
export CUDACXX="${CUDACXX:-/usr/local/cuda/bin/nvcc}"
need git "apt install git"; need cmake "apt install cmake"; need gcc "apt install build-essential"
need g++ "apt install build-essential"; need nvcc "JetPack CUDA toolkit (/usr/local/cuda/bin)"
need ldd "glibc"; need readelf "apt install binutils"; need sha256sum "coreutils"; need tar "tar"
need python3 "python3 (3.10 on JetPack 6)"
CMAKE_VERSION="$(cmake --version | head -n1 | awk '{print $3}')"
GCC_VERSION="$(gcc -dumpfullversion 2>/dev/null || gcc -dumpversion)"
NVCC_VERSION="$(nvcc --version | sed -n 's/^Cuda compilation tools, release \([0-9.]*\).*$/\1/p' | head -n1)"
# full build identity: "Cuda compilation tools, release 12.6, V12.6.68" -> 12.6.68 (what CMake records)
NVCC_FULL_VERSION="$(nvcc --version | sed -nE 's/.*V([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' | head -n1)"
[[ -n "$NVCC_FULL_VERSION" ]] || NVCC_FULL_VERSION="$NVCC_VERSION"
CUDA_VERSION="$(python3 -c 'import json;print(json.load(open("/usr/local/cuda/version.json"))["cuda"]["version"])' 2>/dev/null || echo "unknown")"
CUDA_OBS="$(printf '%s' "$CUDA_VERSION" | sed -nE 's/^([0-9]+\.[0-9]+).*/\1/p')"
echo "   cmake $CMAKE_VERSION, gcc $GCC_VERSION, nvcc $NVCC_VERSION (V$NVCC_FULL_VERSION), CUDA toolkit $CUDA_VERSION (major.minor: ${CUDA_OBS:-unparsed})"
JETSON_MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
echo "   model: $JETSON_MODEL"

# Fixed-recipe provenance: the artifact is registered against a recipe pinned to one platform tuple.
TUPLE_MATCH=1
[[ "$L4T_OBS" == "$EXPECTED_L4T" ]] || TUPLE_MATCH=0
[[ "$CUDA_OBS" == "$EXPECTED_CUDA" ]] || TUPLE_MATCH=0
if [[ "$TUPLE_MATCH" -eq 1 ]]; then
  ok "platform tuple matches track $TRACK: $ARCH / L4T $EXPECTED_L4T / CUDA $EXPECTED_CUDA (JetPack ${EXPECTED_JETPACK:-not established})"
elif [[ "$ALLOW_MISMATCH" -eq 1 ]]; then
  echo "   [warning] platform tuple L4T ${L4T_OBS:-?} / CUDA ${CUDA_OBS:-?} differs from the recipe ($EXPECTED_L4T / $EXPECTED_CUDA); continuing because --allow-tuple-mismatch was given. The receipt records tuple_mismatch_allowed=true and the server may refuse registration against the fixed recipe."
else
  fail "platform tuple L4T ${L4T_OBS:-?} / CUDA ${CUDA_OBS:-?} does not match track $TRACK ($EXPECTED_L4T / $EXPECTED_CUDA). Select the track that matches this host (--track jp623 for L4T 36.5.2, --track l4t3647 for L4T 36.4.7) and register under a recipe created for that track; --allow-tuple-mismatch is for an explicitly unsupported tuple only (the receipt will say so)"
fi
FREE_MB="$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)"
echo "   MemAvailable: ${FREE_MB} MB, build jobs: $JOBS"
[[ "$FREE_MB" -ge 3000 ]] || echo "   [warning] less than 3 GB available; CUDA compilation may be OOM-killed (stop the robot workload, add swap, or lower --jobs)"
mkdir -p "$WORK" "$OUT"

# ---------------------------------------------------------------- 1. sources ----------------------
log "sources: $LLAMA_REPO @ $LLAMA_COMMIT ($LLAMA_TAG)"
if [[ "$SKIP_CLONE" -eq 0 ]]; then
  if [[ ! -d "$SRC/.git" ]]; then
    git clone --no-checkout "$LLAMA_REPO" "$SRC"
  fi
  git -C "$SRC" fetch --tags origin "$LLAMA_COMMIT" || git -C "$SRC" fetch --tags origin
fi
git -C "$SRC" checkout --detach --force "$LLAMA_COMMIT"
HEAD="$(git -C "$SRC" rev-parse HEAD)"
[[ "$HEAD" == "$LLAMA_COMMIT" ]] || fail "HEAD $HEAD != pinned $LLAMA_COMMIT"
ok "HEAD == $LLAMA_COMMIT"
if TAG_COMMIT="$(git -C "$SRC" rev-list -n1 "$LLAMA_TAG" 2>/dev/null)"; then
  [[ "$TAG_COMMIT" == "$LLAMA_COMMIT" ]] && ok "tag $LLAMA_TAG points at the pinned commit" \
    || echo "   [warning] tag $LLAMA_TAG resolves to $TAG_COMMIT, not the pinned commit; the commit is authoritative"
else
  echo "   [note] tag $LLAMA_TAG not present locally; the commit is authoritative"
fi
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || fail "source tree is dirty; refusing to build a non-reproducible artifact"
ok "clean source tree"

# ---------------------------------------------------------------- 2. clean build (or verified reuse) --
# Invocation = recipe flags with the CUDA architectures entry typed (:STRING). An untyped -DVAR=value is
# cached as UNINITIALIZED; the value is what matters and it is unchanged.
CMAKE_INVOCATION=()
for f in "${CMAKE_FLAGS[@]}"; do
  if [[ "$f" == -DCMAKE_CUDA_ARCHITECTURES=* ]]; then
    CMAKE_INVOCATION+=("-DCMAKE_CUDA_ARCHITECTURES:STRING=${f#-DCMAKE_CUDA_ARCHITECTURES=}")
  else
    CMAKE_INVOCATION+=("$f")
  fi
done
cache_value() { sed -nE "s/^$1:[A-Z]+=(.*)$/\1/p" "$BUILD/CMakeCache.txt" | head -n1; }
CXX_COMPILER=""; CXX_VERSION=""; BUILD_STEPS=""
if [[ "$REUSE_BUILD" -eq 1 ]]; then
  log "reuse existing build tree: validated INCREMENTAL build (no configure, no clean): $BUILD"
  # 1) identity, by inspection only (scripts/jetson/reuse-build-check.sh): pinned source, exact recipe
  #    cache entries, the toolchain CMake recorded (paths in the cache, versions in
  #    CMakeFiles/<ver>/CMake<LANG>Compiler.cmake) equal to the host's full nvcc version and to what the
  #    recorded C++ compiler reports itself, bin/llama-server present
  CHECK_OUT="$("$(dirname "${BASH_SOURCE[0]}")/reuse-build-check.sh" --build "$BUILD" --src "$SRC" --nvcc "$(command -v nvcc)" \
    --nvcc-version "$NVCC_FULL_VERSION" -- "${CMAKE_FLAGS[@]}")" || { printf '%s\n' "$CHECK_OUT"; fail "--reuse-build: the existing tree did not validate (see above); nothing was built or changed"; }
  printf '%s\n' "$CHECK_OUT" | grep -v '^[a-z_]*=' || true
  CXX_COMPILER="$(printf '%s\n' "$CHECK_OUT" | sed -n 's/^cxx_compiler=//p')"
  CXX_VERSION="$(printf '%s\n' "$CHECK_OUT" | sed -n 's/^cxx_version=//p')"
  CACHE_BEFORE="$(sha256sum "$BUILD/CMakeCache.txt" | awk '{print $1}')"
  # 2) the incremental build. It is EXECUTED and recorded truthfully: an up-to-date tree does nothing,
  #    an unfinished one compiles/links what is missing (the receipt says how many steps ran).
  BUILD_LOG="$OUT/reuse-build.log"
  cmake --build "$BUILD" --target llama-server -j "$JOBS" 2>&1 | tee "$BUILD_LOG"
  [[ "${PIPESTATUS[0]}" -eq 0 ]] || fail "--reuse-build: cmake --build failed (log: $BUILD_LOG)"
  BUILD_STEPS="$(grep -cE '(Building|Linking) ' "$BUILD_LOG" || true)"
  # 3) revalidate: the build must not have changed the identity it was validated on
  [[ "$(sha256sum "$BUILD/CMakeCache.txt" | awk '{print $1}')" == "$CACHE_BEFORE" ]] || fail "--reuse-build: CMakeCache.txt changed during the build (a reconfigure happened); rerun a full build"
  [[ "$(git -C "$SRC" rev-parse HEAD)" == "$LLAMA_COMMIT" && -z "$(git -C "$SRC" status --porcelain)" ]] || fail "--reuse-build: source tree changed during the build"
  ok "incremental build executed: $BUILD_STEPS compile/link step(s) reported; cache and source identity unchanged"
else
  log "configure + build (clean directory: $BUILD)"
  rm -rf "$BUILD"
  echo "   cmake ${CMAKE_INVOCATION[*]}"
  cmake -S "$SRC" -B "$BUILD" "${CMAKE_INVOCATION[@]}"
fi
# Cache checks compare the VALUE independent of the cache entry type.
grep -Eq '^GGML_CUDA:[A-Z]+=ON$' "$BUILD/CMakeCache.txt" || fail "CMake cache does not show GGML_CUDA=ON"
CUDA_ARCH_CACHE="$(cache_value CMAKE_CUDA_ARCHITECTURES)"
[[ "$CUDA_ARCH_CACHE" == "87" ]] || fail "CMake cache CMAKE_CUDA_ARCHITECTURES is '${CUDA_ARCH_CACHE:-unset}', expected 87"
ok "CMake cache: GGML_CUDA=ON, CMAKE_CUDA_ARCHITECTURES=87 ($(grep -E '^CMAKE_CUDA_ARCHITECTURES:' "$BUILD/CMakeCache.txt" | head -n1))"
if [[ "$REUSE_BUILD" -eq 0 ]]; then
  cmake --build "$BUILD" --target llama-server -j "$JOBS"
fi
BIN="$BUILD/bin/llama-server"
[[ -x "$BIN" ]] || fail "expected $BIN after the build"
ok "built $BIN"

# ---------------------------------------------------------------- 3. package closure --------------
log "package: enumerate shared-library closure with ldd"
rm -rf "$PKG" && mkdir -p "$PKG/bin" "$PKG/lib"
cp --dereference "$BIN" "$PKG/bin/llama-server"
chmod 0755 "$PKG/bin/llama-server"

is_system_path() {
  local p="$1" pre
  for pre in "${SYSTEM_LIB_PREFIXES[@]}"; do
    [[ "$p" == "$pre"/* ]] && return 0
  done
  return 1
}

declare -A BUNDLED=()      # soname/needed name -> real file path in the build tree
declare -a SYSTEM_DEPS=()  # "name => path" entries for the receipt
LDD_OUT="$(ldd "$BIN")"
while IFS= read -r l; do echo "   ldd: $l"; done <<< "$LDD_OUT"
while IFS= read -r line; do
  [[ "$line" == *"=>"* ]] || continue                      # skip the vdso / interpreter lines
  name="$(echo "$line" | awk '{print $1}')"
  path="$(echo "$line" | awk '{print $3}')"
  if [[ "$line" == *"not found"* ]]; then
    fail "ldd: $name not found; the build closure is incomplete"
  fi
  [[ -n "$path" && "$path" != "(0x"* ]] || continue
  if [[ "$path" == "$BUILD"/* ]]; then
    BUNDLED["$name"]="$path"
  elif is_system_path "$path"; then
    SYSTEM_DEPS+=("$name => $path")
  else
    fail "ldd resolved $name to unexpected location $path (neither build tree nor system prefix); refusing to guess"
  fi
done <<< "$LDD_OUT"

# Also bundle every shared object the build produced, even if not (yet) in the ldd closure
# (e.g. a backend loaded on demand): they are part of the same reproducible build.
while IFS= read -r so; do
  [[ -f "$so" && ! -L "$so" ]] || continue
  bn="$(basename "$so")"
  [[ -n "${BUNDLED[$bn]:-}" ]] || BUNDLED["$bn"]="$so"
done < <(find "$BUILD/bin" "$BUILD/src" "$BUILD/ggml" "$BUILD/common" "$BUILD/tools" -maxdepth 3 -name '*.so*' 2>/dev/null | sort)
[[ "${#BUNDLED[@]}" -gt 0 ]] || fail "no shared libraries found in the build tree (was a static build produced?)"

soname_of() { readelf -d "$1" 2>/dev/null | sed -n 's/.*(SONAME) *Library soname: \[\(.*\)\]$/\1/p' | head -n1; }

# Materialise: copy the real file, plus a regular-file copy under its SONAME and under the NEEDED name.
for needed in "${!BUNDLED[@]}"; do
  real="$(readlink -f "${BUNDLED[$needed]}")"
  realname="$(basename "$real")"
  soname="$(soname_of "$real")"
  for target in "$realname" "$soname" "$needed"; do
    [[ -n "$target" ]] || continue
    if [[ -e "$PKG/lib/$target" ]]; then
      cmp -s "$real" "$PKG/lib/$target" || fail "conflicting library content for $target"
    else
      cp --dereference "$real" "$PKG/lib/$target"
      chmod 0644 "$PKG/lib/$target"
    fi
  done
  echo "   bundled: $needed (real=$realname soname=${soname:-none})"
done
for d in "${SYSTEM_DEPS[@]}"; do echo "   system : $d"; done
if ! find "$PKG" -type l | grep -q .; then ok "no symlinks in the package"; else fail "package contains symlinks"; fi
grep -q 'libggml-cuda' <<< "$(printf '%s\n' "${!BUNDLED[@]}")" && ok "libggml-cuda is in the closure" \
  || echo "   [warning] libggml-cuda.so is not in the closure; the runtime would fall back to CPU and fail the intended-backend gate"
RPATH_INFO="$(readelf -d "$PKG/bin/llama-server" | grep -E 'RPATH|RUNPATH' || true)"
echo "   binary RPATH/RUNPATH: ${RPATH_INFO:-none}"

# ---------------------------------------------------------------- 4. out-of-tree smoke -------------
log "smoke test OUTSIDE the build tree (build tree hidden so RPATH/RUNPATH cannot help)"
rm -rf "$VERIFY" && mkdir -p "$VERIFY"
cp -a "$PKG/." "$VERIFY/"
HIDDEN="$BUILD.hidden-$$"
mv "$BUILD" "$HIDDEN"
restore_build() { [[ -d "$HIDDEN" ]] && mv "$HIDDEN" "$BUILD"; }
trap restore_build EXIT
LDD_PKG="$(LD_LIBRARY_PATH="$VERIFY/lib" ldd "$VERIFY/bin/llama-server")"
if grep -q "not found" <<< "$LDD_PKG"; then
  echo "$LDD_PKG" >&2; fail "packaged llama-server has unresolved libraries outside the build tree"
fi
while IFS= read -r line; do
  [[ "$line" == *"=>"* ]] || continue
  path="$(echo "$line" | awk '{print $3}')"
  [[ -n "$path" && "$path" != "(0x"* ]] || continue
  if [[ "$path" != "$VERIFY/lib/"* ]] && ! is_system_path "$path"; then
    fail "packaged binary resolved a library from $path (not the package, not the system)"
  fi
done <<< "$LDD_PKG"
ok "every library resolves to the package or a system prefix"
VERSION_OUT="$(LD_LIBRARY_PATH="$VERIFY/lib" "$VERIFY/bin/llama-server" --version 2>&1 || true)"
echo "   --version: $VERSION_OUT"
grep -q "${LLAMA_COMMIT:0:7}" <<< "$VERSION_OUT" || fail "--version output does not mention commit ${LLAMA_COMMIT:0:7}"
ok "--version mentions the pinned commit"
LD_LIBRARY_PATH="$VERIFY/lib" "$VERIFY/bin/llama-server" --help >/dev/null 2>&1 || fail "--help failed"
ok "--help runs"
restore_build; trap - EXIT

# ---------------------------------------------------------------- 5. hashes, receipt, archive ------
log "hash files, write receipt.json and archive"
ARCHIVE="$OUT/runtime-$LLAMA_COMMIT-$ARCH.tar.gz"
RECEIPT="$OUT/receipt.json"
# Deterministic, canonical member names: `bin/llama-server`, `lib/<name>` (no ./, no symlinks).
tar --format=gnu --sort=name --owner=0 --group=0 --numeric-owner --mtime='2020-01-01 00:00:00Z' \
    -C "$PKG" -czf "$ARCHIVE" bin lib
ARCHIVE_SHA="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
ARCHIVE_SIZE="$(stat -c %s "$ARCHIVE")"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# python3 writes exact JSON types (the server schema is strict: ints must be ints, no extra keys).
PKG_DIR="$PKG" ARCHIVE_PATH="$ARCHIVE" ARCHIVE_SHA="$ARCHIVE_SHA" ARCHIVE_SIZE="$ARCHIVE_SIZE" \
RECEIPT_PATH="$RECEIPT" LLAMA_COMMIT="$LLAMA_COMMIT" LLAMA_TAG="$LLAMA_TAG" CMAKE_VERSION="$CMAKE_VERSION" \
GCC_VERSION="$GCC_VERSION" NVCC_VERSION="$NVCC_VERSION" CUDA_VERSION="$CUDA_VERSION" L4T_LINE="$L4T_LINE" \
BUILD_DATE="$BUILD_DATE" HOST_ARCH="$ARCH" JETSON_MODEL="$JETSON_MODEL" VERSION_OUT="$VERSION_OUT" \
JOBS="$JOBS" CMAKE_FLAGS_STR="$(printf '%s\n' "${CMAKE_FLAGS[@]}")" \
CMAKE_INVOCATION_STR="$(printf '%s\n' "${CMAKE_INVOCATION[@]}")" CUDA_ARCH_CACHE="$CUDA_ARCH_CACHE" \
SYSTEM_DEPS_STR="$(printf '%s\n' "${SYSTEM_DEPS[@]}")" \
L4T_OBS="$L4T_OBS" CUDA_OBS="$CUDA_OBS" TUPLE_MATCH="$TUPLE_MATCH" ALLOW_MISMATCH="$ALLOW_MISMATCH" \
EXPECTED_ARCH="$EXPECTED_ARCH" EXPECTED_L4T="$EXPECTED_L4T" EXPECTED_CUDA="$EXPECTED_CUDA" \
EXPECTED_JETPACK="$EXPECTED_JETPACK" EXPECTED_CC="$EXPECTED_CC" TRACK="$TRACK" REUSE_BUILD="$REUSE_BUILD" \
NVCC_FULL_VERSION="$NVCC_FULL_VERSION" CXX_COMPILER="$CXX_COMPILER" CXX_VERSION="$CXX_VERSION" BUILD_STEPS="$BUILD_STEPS" \
python3 - <<'PY'
import hashlib, json, os, subprocess, tarfile
env = os.environ
pkg = env["PKG_DIR"]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()
def soname(p):
    try:
        out = subprocess.run(["readelf", "-d", p], capture_output=True, text=True, check=True).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if "(SONAME)" in line and "[" in line:
            return line[line.index("[") + 1 : line.rindex("]")]
    return None
files = []
for root, _dirs, names in os.walk(pkg):
    for n in sorted(names):
        p = os.path.join(root, n)
        assert not os.path.islink(p), p
        rel = os.path.relpath(p, pkg).replace(os.sep, "/")
        entry = {"path": rel, "sha256": sha(p), "size": os.path.getsize(p),
                 "kind": "executable" if rel.startswith("bin/") else "shared_library"}
        so = soname(p) if rel.startswith("lib/") else None
        if so:
            entry["soname"] = so
        files.append(entry)
files.sort(key=lambda e: e["path"])
assert any(e["path"] == "bin/llama-server" for e in files)
# Re-inspect the archive the way the server does (regular members only, canonical unique names, hashes).
seen = {}
with tarfile.open(env["ARCHIVE_PATH"], "r:gz") as tf:
    for m in tf:
        if m.isdir():
            continue
        assert m.isreg(), f"non-regular member {m.name}"
        parts = [s for s in m.name.split("/") if s not in ("", ".")]
        assert parts and ".." not in parts, m.name
        name = "/".join(parts)
        assert name == m.name, f"non-canonical member name {m.name!r}"
        assert name not in seen, f"duplicate member {name}"
        h = hashlib.sha256()
        src = tf.extractfile(m)
        for c in iter(lambda: src.read(1 << 20), b""):
            h.update(c)
        seen[name] = (h.hexdigest(), m.size)
expected = {e["path"]: (e["sha256"], e["size"]) for e in files}
assert seen == expected, f"archive members differ from the package: {set(seen) ^ set(expected)}"
tuple_match = env["TUPLE_MATCH"] == "1"
jetpack = env["EXPECTED_JETPACK"] or None  # empty = not established for this track: recorded as null
target_expected = {"arch": env["EXPECTED_ARCH"], "os": "linux", "backend": "cuda",
                   "compute_capability": env["EXPECTED_CC"],
                   "l4t": env["EXPECTED_L4T"], "cuda": env["EXPECTED_CUDA"]}
target_observed = {"arch": env["HOST_ARCH"], "os": "linux", "backend": "cuda",
                   "compute_capability": env["EXPECTED_CC"],
                   "l4t": env["L4T_OBS"] or None, "cuda": env["CUDA_OBS"] or None}
if jetpack:
    target_expected["jetpack"] = jetpack
    target_observed["jetpack"] = jetpack if tuple_match else None
receipt = {
    "schema_version": 1,
    "archive_sha256": env["ARCHIVE_SHA"],
    "archive_size": int(env["ARCHIVE_SIZE"]),
    "files": files,
    "provenance": {
        "runtime": "llama.cpp",
        "source_repo": "https://github.com/ggml-org/llama.cpp",
        "commit": env["LLAMA_COMMIT"],
        "tag": env["LLAMA_TAG"],
        "cmake_flags": [f for f in env["CMAKE_FLAGS_STR"].splitlines() if f],
        "cmake_invocation": [f for f in env["CMAKE_INVOCATION_STR"].splitlines() if f],
        "cmake_cache_cuda_architectures": env["CUDA_ARCH_CACHE"],
        "cmake_version": env["CMAKE_VERSION"],
        "gcc_version": env["GCC_VERSION"],
        "nvcc_version": env["NVCC_FULL_VERSION"] or env["NVCC_VERSION"],
        "cxx_compiler": env["CXX_COMPILER"] or None,
        "cxx_compiler_version": env["CXX_VERSION"] or None,
        "cuda_version": env["CUDA_VERSION"],
        "l4t_release": env["L4T_LINE"],
        "track": env["TRACK"],
        "jetpack_established": bool(jetpack),
        "reused_build_tree": env["REUSE_BUILD"] == "1",
        "target_expected": target_expected,
        "target_observed": target_observed,
        "tuple_match": tuple_match,
        "tuple_mismatch_allowed": env["ALLOW_MISMATCH"] == "1" and not tuple_match,
        "jetson_model": env["JETSON_MODEL"],
        "host_arch": env["HOST_ARCH"],
        "build_date": env["BUILD_DATE"],
        "build_jobs": int(env["JOBS"]),
        "builder": "scripts/jetson/build-llama-cpp.sh" + (" --reuse-build" if env["REUSE_BUILD"] == "1" else ""),
        "version_output": env["VERSION_OUT"][:512],
        "verification": {
            "clean_build_dir": env["REUSE_BUILD"] != "1",
            "build_tree": (f"reused: existing tree validated by inspection (pinned source, exact recipe cache entries, recorded "
                           f"toolchain == host), then an INCREMENTAL cmake --build was executed ({env['BUILD_STEPS'] or '0'} compile/link "
                           "step(s) reported) and the identity revalidated; no configure or clean by this run")
                          if env["REUSE_BUILD"] == "1" else "fresh: clean build directory configured and built by this run",
            "incremental_build_steps": int(env["BUILD_STEPS"]) if env["BUILD_STEPS"] else None,
            "out_of_tree_smoke": "passed: ldd resolves to package/system only with the build tree hidden; --version and --help ran",
            "archive_reinspected": True,
        },
    },
    "system_dependencies": [d for d in env["SYSTEM_DEPS_STR"].splitlines() if d],
}
with open(env["RECEIPT_PATH"], "w") as f:
    json.dump(receipt, f, indent=2, sort_keys=True)
    f.write("\n")
print(f"   receipt: {len(files)} files, {len(receipt['system_dependencies'])} system dependencies, tuple_match={tuple_match}")
PY
ok "archive members re-inspected: regular files only, canonical unique names, hashes match the receipt"

log "done"
echo "   archive : $ARCHIVE"
echo "   sha256  : $ARCHIVE_SHA ($ARCHIVE_SIZE bytes)"
echo "   receipt : $RECEIPT"
echo "   track   : $TRACK (L4T $EXPECTED_L4T / CUDA $EXPECTED_CUDA); register under a recipe created with {\"track\": \"$TRACK\"}"
echo "   next    : scripts/jetson/register-runtime.sh --server https://<host> --recipe <rcp_id for track $TRACK> --archive '$ARCHIVE' --receipt '$RECEIPT'"
echo "   status  : build verified locally on this device; Convoy's CUDA-offload evidence is collected when the agent starts this runtime"
