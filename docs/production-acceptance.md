# Public portal acceptance — September 14, 2026 UTC

> Historical acceptance record. The portal pages and browser Chat it verified have
> since been removed; see [the production portal record](production-portal.md).

The public [Convoy demo](https://deployconvoy.com/portal) was verified against the
physical `nano-lab-01` Jetson Orin Nano. The dedicated demo login worked in a real
Chrome browser. No browser responses were mocked during this live verification.
Credentials are supplied privately and are not part of this repository.

## Release and validation

- [Merged implementation, PR #69](https://github.com/useconvoy/app/pull/69)
- Deployed revision: `411e6076d32ef276ee65c2771109a3e937ab7597`
- [Successful AWS deployment](https://github.com/useconvoy/app/actions/runs/34812759937)
- [Passing CI](https://github.com/useconvoy/app/actions/runs/34811997430), tested
  revision `f5d5c0d5e50d40b79bb9f9a206de1cc33f99fecf`. Its Git tree is identical
  to the deployed merge revision.

| Check | Result |
| --- | --- |
| Browser contract tests, including WebKit and mobile projects | 85 passed |
| Portal server API tests | 8 passed |
| Deployment and rollback contract tests | 8 passed |
| Control-plane tests | 189 passed, 5 skipped |
| Agent tests on Python 3.10 | 180 passed, 2 skipped |
| Live browser console | No errors or warnings observed |
| Live Chat at 390-pixel viewport | No document overflow; conversation and metrics remained readable |

The skipped tests are not claimed as coverage. Real-device smoke results below
are separate from CI fixture tests and do not qualify arbitrary model runtimes.

## Actual browser conversation

The active release was `rel_7horo87k6lxs`: Qwen2.5-1.5B-Instruct Q4_K_M, served
by llama.cpp with CUDA on the Nano, with a 2,048-token context and a 128-token
output cap.

| Prompt | Actual reply | Browser round trip | Gateway latency | Input / output tokens | Matching trace |
| --- | --- | --- | --- | --- | --- |
| My rover is named Cedar. Reply only with its name. | Cedar | 1.28 s | 308.29 ms | 41 / 3 | `tr_739a29681cac1132` |
| What is my rover named? Reply only with its name. | Cedar | 1.25 s | 273.70 ms | 65 / 3 | `tr_6c523ea8d6e2f8f3` |

Both submissions returned HTTP 202 and were polled to a succeeded result. The
second request included the completed first turn. **Inspect trace** opened the
second result's matching record; both records were present in the received sample.
Browser latency includes the cloud relay and polling. The gateway clock measures
on-device request handling. These two short replies are observations, not a
general benchmark or service-level promise. Replies arrive complete rather than
as a token stream.

## Device evidence

The snapshot fetched at `2026-09-14T06:23:08.584Z` reported the physical device
online, its production gateway running, and Chat eligible. It contained 120 real
telemetry samples and 20 received inference traces. The latest device sample was
dated `2026-09-14T06:22:57.957732Z`; missing or unknown metadata remained explicit.

The Usage view showed 6,814 inference requests, 377,391 input tokens, and 48,944
output tokens for this device over August 16–September 14 UTC. These are received
device counters, including other recorded inference on the same Nano. They are
not unique users, customer traffic, or the two requests from this browser alone.
Buffered reports can revise these totals.

Desktop Device, Chat, Usage, Traces, landing/login, and mobile Chat screenshots
were captured from the public application and delivered in the operator handoff.
The [walkthrough and architecture](production-portal.md) explain the values and
how to repeat the demo.

## Hosting and operating boundary

The deployment reused the existing Lightsail instance. No new instance, managed
database, load balancer, disk, registry, or DNS resource was created. This does not
assert that the existing AWS account or instance is free of charges.

The Jetson makes outbound HTTPS requests to AWS. Its model gateway remains local
to the board; no public inference port or public SSH forwarding was added. Live
cloud inference and evidence were verified after removing the Mac reverse tunnel.
Public operator API and API documentation probes returned HTTP 404, while an
unauthenticated device report returned HTTP 401.

Keep the Jetson powered and connected to the Internet for live Chat. The portal
provides a bounded text-inference demo with physical-device evidence. Public
deployment, enrollment, model switching, fleet administration, and robot motion
control are outside this demo's scope.
