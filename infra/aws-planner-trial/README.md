# Unapplied remote planner trial

This separate Terraform root describes one ephemeral Linux ARM64 planner in AWS. The management API, private mission signer, action policy and MuJoCo simulator stay local. Nothing in this directory has been applied or qualified in an AWS account. The example uses placeholders; an account, region, DNS/certificate, image and spending window have not been selected or approved.

It requires an owned-planner image containing the `development-remote-cpu` placement and bounded release-JSON entrypoint support described in [the planner image guide](../planner/README.md). Rebuild and qualify that exact image: changing the pairing contract changes the planner artifact descriptor. Earlier locally qualified images cannot simply be relabeled. The module checks image-reference syntax; it cannot establish what is inside an ECR digest without an actual image qualification.

## Resources and trust boundary

- One Fargate task: ARM64, 2 vCPU, 4 GiB, context 2048, no GPU or autoscaling. This is a proposed trial allocation, not demonstrated cloud latency or capacity. Startup is bounded to 120 seconds; the existing plan budget remains 30 seconds. Models/native assets are baked into the immutable image; startup downloads no model weights.
- Dedicated IPv4 VPC, two public subnets and one HTTPS ALB. Existing issued ACM certificate and user-controlled DNS name are required. Only the configured client CIDRs can reach port 443; no HTTP listener. DNS is created separately using the output target. Clients must verify hostname/certificate, without TLS bypasses.
- Task public IP provides HTTPS egress for ECR, secrets and logs, avoiding a NAT gateway. The task accepts port 8080 only from the ALB security group. Native model and gateway are loopback-only. TLS terminates at the ALB; ALB-to-task HTTP crosses the isolated VPC. This is not end-to-end TLS or a private-task topology. [AWS Fargate networking](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-task-networking.html).
- The application task role has no policies. Its distinct execution role can pull only the supplied ECR repository, publish to this log group and fetch exactly the supplied planner-public-verifier and probe secret ARNs. ECR authorization-token permission necessarily uses `Resource: "*"`. Secret values are never Terraform inputs or data sources. This small module supports the AWS-managed Secrets Manager encryption key; customer-managed KMS keys require a separately reviewed scoped decrypt policy.
- No API/private signing keys, action keys, HMAC, database, web service or evaluation jobs are sent to the task. A secret ARN's name is not proof of its contents: validate the public-only planner document out of band. The entrypoint rejects private/conflicting authority. Public key JSON is consumed into a private file before model startup. Secret updates do not refresh running task environments; pause missions and replace the task deliberately when updating verification/probe configuration. [ECS secret injection](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/specifying-sensitive-data.html).
- ECS and ALB both check `/ready`, which tests live owning state and exact gateway identity; static `/health` is insufficient. Startup grace is 150 seconds, container health start period 120, and stop timeout 60. The owner handles admission closure and bounded cleanup. One desired task, 0% minimum / 100% maximum rollout and disabled AZ rebalancing avoid overlapping deployment owners, at the cost of downtime. Failed deployments do not automatically roll back to an older release. ECS may replace a failed task; that creates a fresh planner incarnation, never permission to replay a mission.
- Logs retain seven days. Task state is ephemeral; logs alone are not complete cleanup evidence. There is no shared filesystem or session adoption. After a task loss, the coordinator must treat any ambiguous mission using its existing unknown/reconciliation rules. Settle or cancel the mission before a planned replacement.

## Immutable deployment inputs

The task keeps the image entrypoint and overrides its command to `serve` with assets, output, host and timing settings. It deliberately omits `--manifest`. The wrapper consumes `CONVOY_PLANNER_RELEASE_JSON` plus `CONVOY_PLANNER_RELEASE_SHA256`, validates the full paired contract and canonical digest, and creates its private release file once. An existing `--manifest` conflicts with injection.

Supply the exact registered outer release with `placement.planner = "development-remote-cpu"`, the requalified planner artifact, unchanged action artifact and original task/budgets. Calculate the digest with `convoy_contracts.execution.canonical_digest`, not a hash of pretty-printed JSON or Terraform's encoding. Terraform performs only early shape checks; the entrypoint authoritatively rejects duplicate/nonfinite JSON, more than 16 KiB, wrong digest or invalid manifests before native launch. Release JSON is public configuration visible in Terraform state and the ECS task definition; do not put credentials or private data in it.

Only these existing references are accepted: same-account/region ECR digest, issued ACM certificate, distinct whole-document public-planner and probe secret ARNs. No secret, certificate, DNS record, image repository or backend bucket is created here. No metadata lookup is used to silently pick an account or region.

## Local checks without AWS access

Use the pinned Terraform 1.14.7 binary, verified against HashiCorp's official SHA256SUMS, and the committed AWS provider 6.62.0 lock file. The shared [AWS workflow](../../.github/workflows/aws-staging-checks.yml) installs that binary once and runs both roots. From the repository root:

```sh
export AWS_EC2_METADATA_DISABLED=true
terraform -chdir=infra/aws-planner-trial fmt -check -recursive
terraform -chdir=infra/aws-planner-trial init -backend=false -input=false -lockfile=readonly
terraform -chdir=infra/aws-planner-trial validate
terraform -chdir=infra/aws-planner-trial test -var-file=example.tfvars
```

The four mocked plans check stopped-by-default configuration, role/secret/release and ingress/readiness boundaries, one-task replacement, mutable-image rejection and credential-reference separation. They use the real provider schema but make no AWS account calls or resources. They cannot prove IAM permissions in a real account, certificate issuance/coverage, regional capacity, DNS, release/image compatibility, performance, request delivery or teardown.

## Reviewable activation sequence — not executed

1. Obtain the explicit account/role, region and two available AZs, unique trial name, source IPv4 CIDRs, domain and existing issued certificate, approved duration/spend limit, and owner responsible for teardown. Confirm ARM64 Fargate availability, vCPU quota, service-linked-role permissions and ECR/image availability. Existing ACLs/SCPs/quota/certificate/secret content have not been inspected by the local checks.
2. Qualify the rebuilt image and save image/source/model/native/runtime digests. Validate the public-only planner key document against the local API's signer export; prepare the two separate secret references through an approved secret workflow. Keep the local API private signing document local. Test the exact release's canonical digest and artifact identity. Record secret version IDs as metadata in the trial receipt; avoid changing AWSCURRENT during the run.
3. Use a private ignored `trial.tfvars` and an existing encrypted/versioned S3 backend with a unique state key and `use_lockfile = true`. Review backend account/region separately: the AWS provider account allowlist does not constrain the S3 backend. No secret values belong in Terraform state, shell history or plan output. Initialize with `init -reconfigure -backend-config=backend.hcl`; produce and inspect a real saved plan with `planner_enabled = false`. That plan is still billable: it creates the ALB. Only apply an explicitly approved plan within its spending window.
4. Configure the selected DNS record outside this module using `dns_target`; verify it resolves to this ALB and the ACM certificate covers that exact hostname. With the task stopped, 503 at the matching host is expected; the TLS handshake must still verify. Review a new plan enabling the single task, then apply only with the same authorization. Wait for ECS/ALB readiness; probe with the expected release/profile/runtime/artifact and record the actual task ARN, task definition, image digest and account/region. A declared placement or Terraform output alone does not prove hosting.
5. Use the existing external-planner/public-key pipeline over verified HTTPS. Keep the local action/API/simulator processes owned locally; do not let their cleanup kill or manage the remote endpoint. Run the fixed seed with an authenticated real plan, check accepted plan and action evidence, then exercise cancellation and remote planner loss/replacement within a separately approved bounded trial. Record original deadline/authorization failures honestly. One run is not a throughput, networking handoff or real-robot qualification.

No `apply`, login, ECR push, image/model test or AWS account query is part of the local validation above.

## Costed example and complete teardown

Planning example only: US West (Oregon), `us-west-2`, Linux ARM64, one 2-vCPU/4-GiB task, 24 hours (or 730 hours for comparison). Public AWS regional price lists checked September 28, 2026 give $0.03238/vCPU-hour and $0.00356/GiB-hour, so task compute is $0.079/hour. [Fargate rates](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/current/us-west-2/index.json), [billing rules](https://aws.amazon.com/fargate/pricing/).

One ALB costs $0.0225/hour plus $0.008/LCU-hour; public IPv4 costs $0.005/address-hour. With one LCU and at least three public IPv4 addresses (two ALB, one task), subtotal is **$0.1245/hour, $2.99/24 hours or $90.89/730 hours**. [Regional ALB rates](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSELB/current/us-west-2/index.json), [ALB billing](https://aws.amazon.com/elasticloadbalancing/pricing/), [public IPv4 pricing](https://aws.amazon.com/vpc/pricing/).

Recompute with actual task-hours, ALB-hours, LCU-hours and address-hours. Add Secrets Manager, ECR storage, log ingestion/storage, state storage, applicable data transfer/cross-zone/Internet egress, DNS/domain costs and tax. NAT gateway hourly/data-processing cost is zero in this topology because no NAT is created. ALB capacity can add addresses/LCUs. This subtotal is neither a spending cap nor authorization. With the task disabled and zero LCUs, the ALB and two IPv4 addresses alone still cost about $0.0325/hour ($23.73/730 hours), before other charges.

At the end of the approved window:

1. Stop admitting missions locally and reconcile/cancel the current mission; retain bounded local plan/action evidence and safe cloud identity/log evidence without key/token contents. Ephemeral runtime files disappear with the task; do not claim process cleanup solely from a healthy request or Terraform completion.
2. Plan/apply `planner_enabled = false`, confirm task termination and target deregistration, then review and execute a destroy plan for this exact isolated root. Confirm the ECS service/cluster/task, ALB/listener/target group, public task IP, security groups, subnets/VPC/IGW, execution/task roles and log group are gone. Destroy removes the seven-day log group immediately; export required evidence first. Do not leave the ALB running as a substitute for teardown.
3. Remove only the trial DNS record that was created outside this module. Retain shared ACM certificates, repositories and secrets. Explicitly decide retention/deletion for trial-only ECR images, both trial-only secret references/versions and their recovery windows, and the state object's version history after successful destroy. These external resources are intentionally not destroyed by Terraform here and may continue to incur charges. There are no database snapshots or S3 artifact buckets in this root.
4. Verify no matching running tasks, ALBs, ENIs/public IPs or other tagged trial resources remain and reconcile actual charges against the approved limit. A budget alert or stopped service does not enforce a cap.

A single ARM64 VM running the same qualified image and a verified HTTPS proxy is a simpler alternative if ALB cost is undesirable; it trades the managed ingress/task lifecycle for host and certificate operations. It is not implemented by this root.
