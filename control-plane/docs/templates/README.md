# Reviewed chat templates

Templates pinned to a release travel with it (`release.template` = name, text, sha256, reviewer) and
are handed to `llama-server --chat-template-file`; the gateway renders every request with them through
`/apply-template`. A release is immutable, so a template change is a new release.

| file | for | status |
|---|---|---|
| `qwen3-nonthinking.jinja` | Qwen/Qwen3-0.6B-GGUF `Qwen3-0.6B-Q8_0.gguf` (rev `23749fef…`), Qwen/Qwen3-4B-GGUF `Qwen3-4B-Q4_K_M.gguf` (rev `bc640142…`) | experimental condition, not device-verified until the render check below passes on the board |

`qwen3-nonthinking.jinja` is derived from the official Qwen3 template, restricted to what the Convoy
gateway admits (text `system|user|assistant`, no tools/vision, no `chat_template_kwargs`), with the
generation prompt unconditionally ending in the empty think block
(`<|im_start|>assistant\n<think>\n\n</think>\n\n`) that the official template emits for
`enable_thinking=false`. It does not rely on a `/no_think` hint and it does not hide reasoning: the
model is asked not to reason; if it reasons anyway the tokens are counted and reported by the
benchmark (`finish_reason=length`, token counts), never stripped.

## Verified subset (what "equivalent to the official template" is claimed for)

The equivalence claim is limited to the message shapes that were checked against the official
template's output:

- **a single `system` turn followed by a single `user` turn** with the generation prompt appended
  (the planned single-user-turn benchmark); the fixed generation prefix matches upstream for
  `enable_thinking=false`;
- **multi-turn only when every assistant turn precedes the last user turn and its reasoning was
  stripped** (the official template drops `<think>…</think>` for assistant turns before the last user
  query; this file strips up to the last `</think>` and removes leading newlines, and renders the
  remainder as `<|im_start|>assistant\nCONTENT<|im_end|>\n`).

**Not covered:** assistant turns **after** the last user query (an assistant prefix to continue, or
any conversation whose final message is not a user turn). The official template renders reasoning
blocks for those turns differently; this file does not reproduce that and makes no claim about it.
Also not covered: tools, multimodal content, and any `chat_template_kwargs`.

`scripts/jetson/bench_gateway.py` contains a local reference rendering (`render_qwen3_nonthinking`) of
exactly this subset; the render check below compares the runtime's rendering against it byte for byte.

## Verification on the device, in this order, before any qualification claim

```bash
# 1. the model's own embedded template (from the hash-verified cached file), for the diff
/opt/convoy-agent/venv/bin/python scripts/jetson/gguf_template.py /var/lib/convoy-agent/cache/models/<sha256>.gguf > embedded.jinja
# 2. the rendered prompt and its token count through the runtime's /apply-template + /tokenize,
#    compared with the local reference rendering; the template file's sha256 is recorded
python3 scripts/jetson/bench_gateway.py --render-check \
    --runtime http://127.0.0.1:<runtime-port> --api-key-file <agent key file> \
    --template docs/templates/qwen3-nonthinking.jinja
#    exit 0 and ok=true only when the runtime rendered the COMPLETE expected bytes, the rendering ends
#    with the empty think block, and /tokenize returned a non-empty list of integer ids; any other
#    outcome exits 1 with a reason (template_missing, rendered_prompt_mismatch, suffix_mismatch,
#    tokenize_failed, ...). Compare prompt_tokens with what the gateway logs at admission.
```

Context stays 2048, one slot, `max_tokens <= 128`; `cache_prompt` is off so no KV-reuse speedup is
claimed. See `docs/BENCHMARK.md` for the benchmark modes, fixtures and evidence fields.
