# Deploying Convoy Labs

Two phases. Phase 0 puts what exists today on a cloud server safely — one
container, one volume, one shared credential — and is what this repo's
Dockerfile/compose file implement. Phase M1 is the reference topology for the
real runtime with real permissions, matching `docs/architecture-v2.md`.

---

## Why not serverless (read this before reaching for Vercel)

The app as built is deliberately stateful in-process: SSE streams for live
traces, fire-and-forget agent loops that outlive the HTTP response, an
in-memory event bus, and a file-backed store. Serverless platforms
(Vercel/Lambda/scale-to-zero Cloud Run) kill all four — responses freeze the
instance, background loops die mid-run, and instances don't share memory or
disk. **Deploy on something that runs a long-lived process**: a VM, Fly.io,
Railway, Render, or Cloud Run with `min-instances=1` + always-allocated CPU.
This constraint dissolves in M1 when the runtime moves to workers and state
moves to Postgres — but that's the order: don't contort the demo onto
serverless first.

---

## Phase 0 — single container on a cloud server (today)

```
internet → TLS proxy (platform or Caddy) → convoy container (:3000) → volume (/data)
```

### Quick start on any Docker host (Hetzner/EC2/Lightsail VM, Fly, Railway)

```bash
export CONVOY_BASIC_AUTH="meridian:<strong-password>"   # required
export ANTHROPIC_API_KEY=sk-ant-...                     # optional: live model
export CONVOY_LIVE_MODEL=1                              # optional: live model
docker compose up -d --build
```

TLS: on Fly/Railway/Render it's automatic. On a bare VM, put Caddy in front
(`caddy reverse-proxy --from convoy.example.com --to localhost:3000`) —
Basic auth over plain HTTP is not acceptable even for a demo.

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `CONVOY_BASIC_AUTH` | Yes, if internet-facing | `user:password` shared credential enforced by `src/proxy.ts` on workspace routes and APIs. The public landing, guided tour, metadata assets, and `/api/health` stay open; consequential workspace actions remain gated. The stopgap until M1 identity lands — the approve/reject buttons must not be public. |
| `ANTHROPIC_API_KEY` | For live mode | Enables the real Claude tool loop. |
| `CONVOY_LIVE_MODEL` | For live mode | `1` switches the runtime from the deterministic demo planner to the live model. |
| `CONVOY_MODEL` | No | Model override (default `claude-opus-5`). |
| `CONVOY_DATA_DIR` | Set by image | Where the JSON store lives (`/data`, volume-mounted). Delete the volume to reseed. |

### What this gives you

A URL you can hand to anyone: the full governance surface (gateway, policies,
approvals, evals, promotion, audit) running server-side, with agents
executing against the hermetic connectors — and against the live model if the
key is set. **The agent already "runs with the permissions" here** in the
sense that matters: every tool call goes through the in-process gateway,
which resolves the environment's policy and credentials. What Phase 0 does
NOT give you is real external side effects, per-run isolation, or durability
beyond one host — that's M1.

### Operational notes

- Single replica only. The store and event bus are in-process; two replicas
  would split state. Scale-out is an M1 outcome, not a knob to turn here.
- Back up `/data` if the demo state matters; otherwise treat it as cattle
  (`docker volume rm` reseeds Meridian Labs on next boot).
- The container runs as a non-root user, exposes a `/api/health` probe, and
  restarts on failure via compose.

---

## Phase M1 — reference topology for the real runtime with permissions

The question "run the agent on the server with the permissions" becomes, in
cloud terms: **which process may hold which secret, and what can each network
segment reach.** The architecture maps Convoy concepts onto cloud primitives
so the gateway choke point is enforced by infrastructure, not convention.

```
                        ┌─ public subnet ─────────────────────────┐
 internet ── ALB/TLS ──▶│ control plane (Next.js, ECS Fargate)    │
            (+ webhook  │ webhook receiver (sig-verified)         │
             endpoint)  └───────────────┬─────────────────────────┘
                                        │ private
        ┌─ private subnets ─────────────▼─────────────────────────┐
        │ Postgres (RDS)   run queue (Postgres SKIP LOCKED)       │
        │                                                         │
        │ workers (ECS) ── run token only ──▶ gateway (ECS)       │
        │   SG egress: gateway ONLY            │ SG egress:       │
        │   IAM role: none                     │ connector APIs   │
        │                                      ▼                  │
        │                        Secrets Manager + KMS (vault)    │
        │                        readable ONLY by gateway role    │
        └─────────────────────────────────────────────────────────┘
```

### The permissions mapping (the load-bearing table)

| Convoy concept | Cloud primitive that enforces it |
|---|---|
| "Agents never hold credentials" | Workers' IAM task role has **zero** permissions; their security group allows egress **only to the gateway**. Even a fully prompt-injected agent can emit nothing but gateway calls. |
| Per-environment credentials | One Secrets Manager entry per connector instance, KMS-encrypted; resource policy grants decrypt to the **gateway task role only**. Humans and workers cannot read them — not by policy, by IAM. |
| Run-scoped authority | Short-lived JWT minted at lease time (`run_id`, `deployment_id`, expiry); gateway resolves run → deployment → environment → policy itself. A stolen token is time-boxed and attributed. |
| Policy rules / approvals | Postgres, snapshotted into deployments; gateway is the only writer of trace rows. |
| Kill switch | DB flag checked by gateway per call (exists today) **plus** worker-side cancellation on lease heartbeat. |
| Audit | Append-only table (hash-chained later) + CloudTrail covering the vault: every secret decrypt is itself an audited event. |
| Webhook triggers | Public route on the receiver with per-connector signature verification and idempotency keys; everything else private. |

### Sizing the choices

- **Provider**: AWS is the recommendation — not for the tech, for the sales
  posture (the buyer's security review speaks IAM/KMS/VPC, and SOC 2 evidence
  collection is cheapest there). Fly/Railway remain fine for Phase 0 and
  internal staging indefinitely.
- **Queue**: the `runs` table with `FOR UPDATE SKIP LOCKED` — no new infra
  until throughput demands SQS.
- **Sandbox**: Stage A (worker child process, gateway-only egress) ships with
  this topology for free via security groups. Stage B (per-run
  containers/Firecracker) only when custom *code* tools land.
- **Hermetic connectors**: the mock connectors (grown gradually, per plan)
  run as an executor inside the gateway service — eval traffic never leaves
  the VPC, which also makes eval parallelism free of external rate limits.
  Real catalog connectors add only gateway-egress rules; nothing else changes.

### Migration order from Phase 0

1. Postgres (swap `db.ts`; schema already mirrors the spec's tables).
2. Auth (SSO for the UI; approver identity recorded on decisions).
3. Extract gateway to its own service; vault moves to Secrets Manager.
4. Extract workers + queue + run tokens; lock security groups.
5. Split the trigger/webhook receiver; then the Phase 0 container is just
   the control plane, horizontally scalable at last.

Each step keeps the system deployable — no big-bang cutover.
