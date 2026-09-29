# Planner release loading for a separate host

**Passed:** clean `a7e8bd2ee70d498b8a8983a6bbde615e3b076894` rebuilt the Linux planner image and completed the real HTTPS Qwen/SmolVLA/MuJoCo pipeline using an injected release. One accepted proposal took 23.95 seconds within the existing 30-second budget. The simulated arm completed its task in **54 actions**, all exactly equal to the direct baseline. Mission wall time was 67.49 seconds for 0.675 simulated seconds; this is offline execution.

[PR #94](https://github.com/useconvoy/app/pull/94) lets a deployment supply a bounded public release JSON document and its canonical digest. The entrypoint consumes both environment values, validates the exact paired contract, and creates a private release file without overwriting existing state. File-mounted operation remains available; mixing sources, malformed input or digest mismatch is rejected before model startup. Private mission signing remains with the API.

The new `development-remote-cpu` declaration is available only for the real planner; the action policy stays on the qualified local CPU profile. The console can display it. A declaration does not prove hosting: the qualification above explicitly used `development-local` on Docker Desktop. No WAN, AWS, Jetson, physical controller or real-time performance claim follows from it.

## Validation and artifact

- 52 focused contract, entrypoint and owner tests; TypeScript validation and five platform tests using existing matching dependencies; project-config Ruff and independent review.
- A frozen Linux ARM64 image build using the existing verified model and native archive, with no native recompilation or weight downloads.
- Actual release injection, live release/artifact verification, accepted signed proposal and learned-policy task success over verified HTTPS. Wrong CA rejected before local process/action admission.
- Verified planner/native/gateway shutdown; both temporary networks, containers and credentials removed; all eight existing services unchanged.

Image ID: `sha256:0caf16abf319aa5fbb1d71c22af0bb2f20d8c33196e518ee1461ce79e44ae394`. Planner artifact: `a54891c78f22089e78f700984cc4addc6b688217a92f80083c2f8327b05e767e`. The public receipt records the release, model/build lineage and measured result. This image is local and has not been pushed to a cloud registry.

To repeat the [network harness](network-planner.md), use this exact image and add `--inject-release`; retain the existing pinned action assets and direct reference files. The unchanged ownership fault matrix was qualified in the earlier Linux image slice, rather than counted again here.

The separate AWS trial template requires an approved account, verified DNS/certificate and a reviewed cost plan. Local entrypoint qualification is complete; genuine hosted acceptance remains pending. GitHub jobs have been blocked by account billing, so no hosted CI success is claimed.
