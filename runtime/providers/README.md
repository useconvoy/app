# Runtime provider adapters

The application hands a validated `RunSpec` to a provider and consumes only
`RunEvent` records plus the final result.

Planned adapters:

- `local-docker` — implemented in `runtime/local`.
- `agentcore` — map a run to a versioned ARM64 Runtime image and isolated
  `runtimeSessionId`; stream runtime output into `RunEvent`.
- `ecs-ec2` — map a runtime profile to an ECS task definition and use `RunTask`
  against a capacity provider.
- `firecracker` — future dedicated-host pool using OCI-to-rootfs conversion and
  snapshot-compatible host classes.

Provider code must resolve secret references at launch time and must never add
secret values to a resolved RunSpec or event.
