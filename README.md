# Convoy

Convoy combines device inference and robot application deployment in one workspace.
Open `/app` on your deployment: **Projects** manages robots, profiles, fleets, runnable
configurations, simulations and runs; **Configurations** shows saved configurations,
their robots and evals, and the workspace device's live telemetry and traces. Camera
episodes can be replayed from runs and evals. See the
[workspace and replay guide](docs/v1/unified-workspace.md).

The implemented simulation path includes MetaWorld/MuJoCo, a labeled scripted
baseline, a learned SmolVLA policy, and a Qwen text planner selecting a fixed skill.
Local CPU, owned-process activation and external planner endpoints are supported
by the documented reference profiles. This is not general physical-robot support.
See [local activation](docs/v1/local-activation.md), the
[platform design](docs/v1/platform-design.md), and the
[implementation and hosting plan](docs/v1/implementation-plan.md).

Convoy's public website and physical Jetson demo live in this repository.
The landing page introduces the deployment workflow being built for physical AI;
the workspace reads live telemetry and inference traces from one configured Jetson.

- [Website](https://deployconvoy.com/)
- [Jetson demo](https://deployconvoy.com/app) — credentials are supplied privately by the operator
- [Production portal record (historical)](docs/production-portal.md)
- [Website development and verification](website/README.md)
- [Physical verification record](control-plane/docs/VERIFICATION.md)

```text
website/        Next.js landing page, unified workspace, bounded server-side APIs
control-plane/  Python control-plane and outbound device-agent source
integrations/   Simulation, learned action-policy and planner adapters
examples/       Executable experiments and acceptance harnesses
infra/          Local service stacks and optional cloud provisioning
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
