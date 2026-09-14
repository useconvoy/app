# Convoy

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

Production uses the existing Lightsail host. The intended transport is direct
outbound HTTPS from the Jetson agent to AWS, with the operator control plane on
an internal service network. No additional AWS resources are required for this
portal. See the production document for the deployment acceptance boundary and
configuration references; credentials and production secrets never belong in Git.
