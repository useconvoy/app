"""Container-side builder for one pinned, relocatable CPU llama-server artifact."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

COMMIT = "5266f24da75dc449bd56cbed7addb9c8e4a6a73e"
SOURCE_EPOCH = "1788524558"
ROOT = Path("/work")
FLAGS = {
    "CMAKE_BUILD_TYPE": "Release", "CMAKE_EXPORT_COMPILE_COMMANDS": "ON", "BUILD_SHARED_LIBS": "ON",
    "CMAKE_BUILD_WITH_INSTALL_RPATH": "ON", "CMAKE_INSTALL_RPATH": "$ORIGIN;$ORIGIN/../lib",
    "CMAKE_C_FLAGS": "-march=armv8-a", "CMAKE_CXX_FLAGS": "-march=armv8-a",
    "GGML_NATIVE": "OFF", "GGML_CPU_ARM_ARCH": "armv8-a", "GGML_CPU": "ON",
    "GGML_CPU_ALL_VARIANTS": "OFF", "GGML_BACKEND_DL": "OFF", "GGML_METAL": "OFF", "GGML_CUDA": "OFF",
    "GGML_BLAS": "OFF", "GGML_ACCELERATE": "OFF", "GGML_CPU_KLEIDIAI": "OFF", "GGML_OPENMP": "OFF",
    "GGML_RPC": "OFF", "GGML_VULKAN": "OFF", "GGML_SYCL": "OFF", "GGML_LLAMAFILE": "OFF",
    "GGML_CCACHE": "OFF", "LLAMA_OPENSSL": "OFF", "LLAMA_BUILD_UI": "OFF", "LLAMA_USE_PREBUILT_UI": "OFF",
    "LLAMA_BUILD_TESTS": "OFF", "LLAMA_BUILD_EXAMPLES": "OFF", "LLAMA_BUILD_APP": "OFF",
    "LLAMA_BUILD_SERVER": "ON", "LLAMA_BUILD_TOOLS": "ON", "LLAMA_BUILD_COMMON": "ON",
    "LLAMA_BUILD_NUMBER": "1", "LLAMA_BUILD_COMMIT": COMMIT[:7],
}


def run(*command: str) -> str:
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def elf(path: Path) -> dict:
    header = run("readelf", "-h", str(path))
    if "AArch64" not in header or "ELF64" not in header:
        raise ValueError("artifact is not a Linux ARM64 ELF")
    dynamic = run("readelf", "-d", str(path))
    needed = re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic)
    rpaths = re.findall(r"\((?:RUNPATH|RPATH)\).*?\[(.*?)\]", dynamic)
    if any(part and not part.startswith("$ORIGIN") for value in rpaths for part in value.split(":")):
        raise ValueError("artifact contains a nonrelocatable library search path")
    versions = sorted(set(re.findall(r"(?:GLIBCXX|GLIBC|CXXABI)_[0-9.]+", run("readelf", "--version-info", str(path)))))
    return {"needed": needed, "rpaths": rpaths, "required_symbol_versions": versions}


def archive(root: Path, target: Path) -> None:
    with (
        target.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as bundle,
    ):
        for path in sorted(root.rglob("*")):
            info = bundle.gettarinfo(str(path), str(path.relative_to(root)))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.pax_headers = {}
            if path.is_file():
                with path.open("rb") as stream:
                    bundle.addfile(info, stream)
            else:
                bundle.addfile(info)

def main() -> None:
    if platform.system() != "Linux" or platform.machine() != "aarch64":
        raise ValueError("this recipe builds Linux aarch64 only")
    request = json.loads((ROOT / "request.json").read_text())
    if request != {"source_commit": COMMIT, "source_sha256": sha(ROOT / "source.tar")}:
        raise ValueError("source archive does not match the host-verified request")
    source, build, output = ROOT / "source", ROOT / "build", ROOT / "output"
    source.mkdir()
    # Input is the host's exact trusted git archive, extracted only in this
    # disposable, network-disabled, non-root build container.
    subprocess.run(["tar", "-xf", str(ROOT / "source.tar"), "-C", str(source)], check=True)
    os.environ["SOURCE_DATE_EPOCH"] = SOURCE_EPOCH
    subprocess.run(["cmake", "-S", str(source), "-B", str(build), "-G", "Ninja",
                    *(f"-D{key}={value}" for key, value in FLAGS.items())], check=True)
    commands = json.loads((build / "compile_commands.json").read_text())
    for item in commands:
        command = item.get("command", " ".join(item.get("arguments", [])))
        if "-march=armv8-a" not in command or re.search(r"-m(?:arch|cpu|tune)=native", command):
            raise ValueError("compile command lacks the portable ARM baseline")
    subprocess.run(["cmake", "--build", str(build), "--target", "llama-server", "--parallel", "2"], check=True)
    runtime = output / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "lib").mkdir()
    binary = runtime / "bin/llama-server"
    shutil.copy2(build / "bin/llama-server", binary)
    pending, closure = [binary], []
    while pending:
        path = pending.pop(0)
        detail = elf(path)
        closure.append({"path": str(path.relative_to(runtime)), "sha256": sha(path), **detail})
        for needed in detail["needed"]:
            if not needed.startswith(("libllama", "libggml", "libmtmd")):
                continue
            target = runtime / "lib" / needed
            if target.exists():
                continue
            candidates = {candidate.resolve() for candidate in build.rglob(needed) if candidate.is_file()}
            if len(candidates) != 1:
                raise ValueError("native shared library cannot be resolved unambiguously")
            shutil.copy2(candidates.pop(), target)
            pending.append(target)
    system = {}
    for item in closure:
        loaded = run("ldd", str(runtime / item["path"]))
        if "not found" in loaded:
            raise ValueError("native library closure is incomplete")
        for line in loaded.splitlines():
            matches = re.findall(r"(/[^\s()]+)", line)
            for value in matches:
                path = Path(value).resolve()
                if path.is_relative_to(runtime):
                    continue
                if not str(path).startswith(("/usr/lib/", "/lib/")):
                    raise ValueError("unexpected dependency outside packaged/system libraries")
                system[str(path)] = {"sha256": sha(path), "soname": path.name}
    version = run(str(binary), "--version").strip()
    if COMMIT[:7] not in version:
        raise ValueError("binary does not report the pinned source commit")
    help_text = run(str(binary), "--help")
    if "--api-key-file" not in help_text or "--no-warmup" not in help_text:
        raise ValueError("native binary lacks required supervisor controls")
    manifest = {"schema_version": 1, "source": {"repo": "https://github.com/ggml-org/llama.cpp", "commit": COMMIT,
        "dirty": False}, "source_archive_sha256": request["source_sha256"], "platform": {"system": "Linux", "machine": "aarch64"},
        "configuration": FLAGS, "build_jobs": 2, "compiler": run("c++", "--version").strip(),
        "cmake_version": run("cmake", "--version").splitlines()[0], "libc": run("getconf", "GNU_LIBC_VERSION").strip(),
        "packages": run("dpkg-query", "-W", "-f=${Package}=${Version}\\n").splitlines(),
        "closure": closure, "external_libraries": system, "version": version,
        "validation": {"baseline_compile_commands_checked": len(commands), "non_system_closure_packaged": True,
                       "version_passed": True, "help_passed": True, "model_inference_tested": False}}
    (runtime / "build-manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    archive_path = output / "llama-cpp-5266f24-linux-aarch64-cpu.tar.gz"
    archive(runtime, archive_path)
    second = output / "archive-recheck.tar.gz"
    archive(runtime, second)
    if sha(second) != sha(archive_path):
        raise ValueError("archive packaging is not deterministic")
    second.unlink()
    # Qualify relocation separately, without a build-tree library search path.
    fresh = ROOT / "relocated"
    fresh.mkdir()
    subprocess.run(["tar", "-xf", str(archive_path), "-C", str(fresh)], check=True)
    if run(str(fresh / "bin/llama-server"), "--version").strip() != version:
        raise ValueError("freshly extracted native artifact failed relocation")
    result = {"archive_sha256": sha(archive_path), "binary_sha256": sha(binary), "archive_bytes": archive_path.stat().st_size,
              "libraries": [{"path": item["path"], "sha256": item["sha256"]} for item in closure if item["path"].startswith("lib/")],
              "version": version, "deterministic_archive_verified": True, "fresh_extraction_version_passed": True}
    (output / "build-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Native artifact built and verified; no model inference was performed.", flush=True)


if __name__ == "__main__":
    main()
