# Convoy

V1 platform work is planned on the `v1` branch. See the [platform design](docs/v1/platform-design.md)
and [implementation and hosting plan](docs/v1/implementation-plan.md) for milestones,
service boundaries, testing, infrastructure, and the distinction between planned and implemented behavior.

Convoy's public website and physical Jetson demo live in this repository.
The landing page introduces the deployment workflow being built for physical AI;
the portal exposes a focused, working text-inference path on one configured Jetson.

- [Website](https://deployconvoy.com/)
- [Jetson demo](https://deployconvoy.com/portal) — credentials are supplied privately by the operator
- [Production portal architecture and walkthrough](docs/production-portal.md)
- [Website development and verification](website/README.md)
- [Physical verification record](control-plane/docs/VERIFICATION.md)

```text
website/        Next.js landing page, authenticated portal, bounded server-side API
control-plane/  Python control-plane and outbound device-agent source
docs/           Architecture, walkthrough, and acceptance scope
```

The verified demo configuration is Qwen2.5-1.5B-Instruct Q4_K_M on the project's
NVIDIA Jetson Orin Nano Developer Kit Super 8 GB, served by llama.cpp with CUDA
29/29 layers, a 2,048-token context and a 128-token output cap. This is scoped
text-inference evidence. It does not establish arbitrary model compatibility,
customer workload performance, physical fleet rollout, or robot motion control.

The public portal provides Device, Chat, Usage, and Traces. Its server-side API
selects one explicitly physical device and excludes simulator totals. Demo users
can inspect that device and submit text Chat requests; deployment, enrollment,
configuration, and administrative controls remain outside the demo.

Production runs on the existing Lightsail host. The Jetson agent connects directly
to AWS over outbound HTTPS, with the operator control plane on an internal service
network. Public login, two-turn Chat, telemetry, usage, and matching traces passed
live acceptance on September 14, 2026 UTC without a Mac relay. No new AWS resources
were created; existing account charges still apply. See the
[deployment acceptance record](docs/production-acceptance.md) for the deployed
revision and measured results. Credentials and production secrets never belong in Git.
