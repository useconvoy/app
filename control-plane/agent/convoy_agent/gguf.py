"""Bounded GGUF header parser (stdlib only). Reads only the KV metadata needed for qualification and
budget estimates; never loads tensors. Every count/length is bounded so a hostile file cannot make the
agent allocate unbounded memory.

Spec: magic 'GGUF', u32 version (2 or 3), u64 n_tensors, u64 n_kv, then KV pairs
(string key, u32 type, value). Types: 0 u8,1 i8,2 u16,3 i16,4 u32,5 i32,6 f32,7 bool,8 string,9 array,
10 u64,11 i64,12 f64. Strings are u64 length + bytes."""

from __future__ import annotations

import struct
from typing import Any, BinaryIO

MAX_KV = 4096
MAX_STRING = 1 << 20  # 1 MiB (chat templates can be long)
MAX_ARRAY = 1 << 20  # vocab arrays are ~150k
MAX_TOTAL_META_BYTES = 64 << 20
INTERESTING_PREFIXES = ("general.", "tokenizer.ggml.model", "tokenizer.chat_template", "tokenizer.ggml.pre")
ARCH_KEYS = (
    "block_count",
    "context_length",
    "embedding_length",
    "attention.head_count",
    "attention.head_count_kv",
    "attention.key_length",
    "vocab_size",
)


class GGUFError(ValueError):
    pass


_SCALAR = {
    0: ("<B", 1),
    1: ("<b", 1),
    2: ("<H", 2),
    3: ("<h", 2),
    4: ("<I", 4),
    5: ("<i", 4),
    6: ("<f", 4),
    7: ("<?", 1),
    10: ("<Q", 8),
    11: ("<q", 8),
    12: ("<d", 8),
}


class _Reader:
    def __init__(self, f: BinaryIO):
        self.f = f
        self.read_bytes = 0

    def take(self, n: int) -> bytes:
        if n < 0 or n > MAX_TOTAL_META_BYTES:
            raise GGUFError("length out of bounds")
        b = self.f.read(n)
        if len(b) != n:
            raise GGUFError("truncated header")
        self.read_bytes += n
        if self.read_bytes > MAX_TOTAL_META_BYTES:
            raise GGUFError("metadata too large")
        return b

    def scalar(self, t: int) -> Any:
        fmt, size = _SCALAR[t]
        return struct.unpack(fmt, self.take(size))[0]

    def string(self) -> str:
        (n,) = struct.unpack("<Q", self.take(8))
        if n > MAX_STRING:
            raise GGUFError("string too long")
        return self.take(n).decode("utf-8", errors="replace")

    def value(self, t: int, keep: bool) -> Any:
        if t in _SCALAR:
            return self.scalar(t)
        if t == 8:
            return self.string()
        if t == 9:
            (et,) = struct.unpack("<I", self.take(4))
            (n,) = struct.unpack("<Q", self.take(8))
            if n > MAX_ARRAY:
                raise GGUFError("array too long")
            if et == 9:
                raise GGUFError("nested arrays unsupported")
            out: list[Any] = []
            for _ in range(n):
                v = self.value(et, keep)
                if keep and len(out) < 8:
                    out.append(v)
            return {"__array_len": int(n), "head": out} if keep else None
        raise GGUFError(f"unknown value type {t}")


def read_metadata(path: str) -> dict[str, Any]:
    """Return {'version', 'n_tensors', 'n_kv', 'kv': {...}} keeping only architecture/tokenizer keys.
    Large arrays are summarised as their length; nothing else is retained."""
    with open(path, "rb") as f:
        return read_metadata_from(f)


def read_metadata_from(f: BinaryIO) -> dict[str, Any]:
    """Same as read_metadata over any binary stream positioned at byte 0 (a file, or a bounded HTTP
    range reader on the server). Reads only the header: the stream is never read past the KV section."""
    r = _Reader(f)
    if r.take(4) != b"GGUF":
        raise GGUFError("not a GGUF file")
    (version,) = struct.unpack("<I", r.take(4))
    if version not in (2, 3):
        raise GGUFError(f"unsupported GGUF version {version}")
    (n_tensors,) = struct.unpack("<Q", r.take(8))
    (n_kv,) = struct.unpack("<Q", r.take(8))
    if n_kv > MAX_KV:
        raise GGUFError("too many KV pairs")
    kv: dict[str, Any] = {}
    arch: str | None = None
    for _ in range(n_kv):
        key = r.string()
        (t,) = struct.unpack("<I", r.take(4))
        keep = (
            key.startswith(INTERESTING_PREFIXES)
            or (arch is not None and key.startswith(arch + "."))
            or key.startswith("qwen2.")
            or key.startswith("llama.")
        )
        v = r.value(t, keep)
        if key == "general.architecture" and isinstance(v, str):
            arch = v
        if keep and v is not None:
            if isinstance(v, str) and len(v) > 4096 and key != "tokenizer.chat_template":
                v = v[:4096]
            kv[key] = v
    return {
        "version": version,
        "n_tensors": int(n_tensors),
        "n_kv": int(n_kv),
        "kv": kv,
        "header_bytes": r.read_bytes,
    }


FILE_TYPES = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    7: "Q8_0",
    8: "Q5_0",
    9: "Q5_1",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS",
    20: "IQ2_XS",
    30: "BF16",
}
RECOGNIZED_ARCHS = {"qwen2", "qwen3", "llama", "gemma2", "gemma3", "phi3", "mistral"}
RECOGNIZED_TOKENIZERS = {"gpt2", "llama", "spm", "bpe"}
# Sane bounds for the dimensions that size the KV cache and the compute buffer. A count outside these
# bounds (or zero, negative, fractional, boolean, non-numeric) can never come from a real model file
# and would make the memory estimate meaningless, so the release is refused rather than "sized".
DIMENSION_BOUNDS: dict[str, tuple[int, int]] = {
    "n_layers": (1, 4096),
    "n_kv_heads": (1, 1024),
    "head_dim": (1, 4096),
    "n_embd": (1, 65536),
    "n_vocab": (1, 1 << 22),
}
REQUIRED_DIMENSIONS = ("n_layers", "n_kv_heads", "head_dim")


def strict_int(v: Any) -> int | None:
    """An integer value exactly (bool, float, str and None are NOT integers); else None."""
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v


def dimension_errors(kvi: dict[str, Any] | None) -> dict[str, str]:
    """Validate the KV-estimate inputs derived from the parsed bytes: n_layers, n_kv_heads and head_dim
    are required; n_embd and n_vocab are validated when present. Returns {name: reason} (empty when
    every present dimension is a positive bounded integer)."""
    k = kvi or {}
    errors: dict[str, str] = {}
    for name, (lo, hi) in DIMENSION_BOUNDS.items():
        present = name in k and k[name] is not None
        if not present:
            if name in REQUIRED_DIMENSIONS:
                errors[name] = "missing from the GGUF header"
            continue
        v = k[name]
        iv = strict_int(v)
        if iv is None:
            errors[name] = f"must be an integer, got {type(v).__name__} {v!r}"
        elif iv < lo or iv > hi:
            errors[name] = f"must be within {lo}..{hi}, got {iv}"
    return errors


def qualification(meta: dict[str, Any]) -> dict[str, Any]:
    """Derive the compatibility view: architecture, quant, tokenizer, template presence, budget counts.
    Unknown metadata => needs_qualification. Never substitutes a default template."""
    kv = meta.get("kv", {})
    arch = kv.get("general.architecture")
    ft = kv.get("general.file_type")
    out: dict[str, Any] = {
        "architecture": arch,
        "file_type": FILE_TYPES.get(ft, f"unknown({ft})") if ft is not None else None,
        "tokenizer_model": kv.get("tokenizer.ggml.model"),
        "tokenizer_pre": kv.get("tokenizer.ggml.pre"),
        "has_chat_template": bool(kv.get("tokenizer.chat_template")),
        "name": kv.get("general.name"),
        "quantized_by": kv.get("general.quantized_by"),
    }
    counts = {}
    if arch:
        for k in ARCH_KEYS:
            v = kv.get(f"{arch}.{k}")
            if v is not None:
                counts[k] = v
    out["counts"] = counts
    n_layers = counts.get("block_count")
    # fall back to the attention head count only when the KV head count is ABSENT; a present but invalid
    # value (0, False, ...) is kept so dimension_errors refuses it instead of a plausible substitute
    n_kv = counts.get("attention.head_count_kv")
    if n_kv is None:
        n_kv = counts.get("attention.head_count")
    n_head = counts.get("attention.head_count")
    n_embd = counts.get("embedding_length")
    head_dim = counts.get("attention.key_length")
    if head_dim is None:
        # derived only from exact positive integers that divide evenly; anything else stays unknown and
        # is refused by dimension_errors instead of being truncated into a plausible-looking number
        e, h = strict_int(n_embd), strict_int(n_head)
        head_dim = e // h if (e and h and e > 0 and h > 0 and e % h == 0) else None
    out["kv_estimate_inputs"] = {
        "n_layers": n_layers,
        "n_kv_heads": n_kv,
        "head_dim": head_dim,
        "n_embd": n_embd,
        "n_vocab": counts.get("vocab_size"),
    }
    reasons = []
    if arch not in RECOGNIZED_ARCHS:
        reasons.append(f"architecture {arch!r} not in the recognized list")
    if out["file_type"] is None or str(out["file_type"]).startswith("unknown"):
        reasons.append("file type (quantization) unknown")
    if out["tokenizer_model"] not in RECOGNIZED_TOKENIZERS:
        reasons.append(f"tokenizer {out['tokenizer_model']!r} not recognized")
    if not out["has_chat_template"]:
        reasons.append("no embedded chat template; a reviewed template file must be pinned")
    if not (n_layers and n_kv and head_dim):
        reasons.append("layer/head counts missing; KV cache estimate unavailable")
    out["status"] = "recognized" if not reasons else "needs_qualification"
    out["reasons"] = reasons
    return out


def write_minimal_gguf(path: str, kv: dict[str, Any], pad_bytes: int = 0) -> None:
    """Test/simulator helper: write a header-only GGUF (no tensors) with the given scalar/string KVs."""
    parts = [b"GGUF", struct.pack("<I", 3), struct.pack("<Q", 0), struct.pack("<Q", len(kv))]

    def s(x: str) -> bytes:
        b = x.encode()
        return struct.pack("<Q", len(b)) + b

    for k, v in kv.items():
        parts.append(s(k))
        if isinstance(v, bool):
            parts.append(struct.pack("<I", 7) + struct.pack("<?", v))
        elif isinstance(v, int):
            parts.append(struct.pack("<I", 4) + struct.pack("<I", v))
        elif isinstance(v, float):
            parts.append(struct.pack("<I", 6) + struct.pack("<f", v))
        elif isinstance(v, str):
            parts.append(struct.pack("<I", 8) + s(v))
        else:
            raise GGUFError(f"unsupported test value for {k}")
    data = b"".join(parts)
    with open(path, "wb") as f:
        f.write(data)
        if pad_bytes:
            f.write(b"\0" * pad_bytes)
