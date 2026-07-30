# Recursive-agent infrastructure POC

This image has two modes:

- `coordinator` runs a Temporal Worker, starts one Mission Workflow, and exits
  after the mission completes.
- `agent` runs one bounded Agent Episode in an isolated Fargate task, writes its
  artifact to S3, projects state to DynamoDB, signals its Agent Workflow, and
  exits.

The compiler starts with a governed `MissionSpec`, not a precomputed DAG. Each
Temporal Agent Workflow launches one episode and interprets the episode's typed
next intent. A `spawn_agents` intent is admitted against the mission deadline,
maximum depth, total-agent ceiling, estimated budget, and a DynamoDB-backed
parallel-slot semaphore before child Agent Workflows start.

Each isolated episode calls the Anthropic Messages API for a structured thesis,
supporting findings, and either a bounded child-agent plan or a completion
summary. The default model is `claude-sonnet-5`; `CONVOY_MODEL` can override it.
The API key is injected from the `convoy/dev/anthropic` Secrets Manager secret
and never stored in the image or plain task-definition environment.

There are no always-on ECS services. After the coordinator and agent tasks
exit, the POC has no running compute.

## Deployed POC

The CDK stack lives in `infra/cdk` and is deployed as `ConvoyPocStack` in
AWS account `327998106824`, region `us-west-2`. It creates:

- a capped App Runner service for the authenticated Convoy UI/API;
- a public-only, no-ingress VPC for disposable tasks (no NAT Gateway);
- an ECS Fargate cluster and separate agent/coordinator task definitions;
- an ECR repository for the shared ARM64 image;
- an encrypted, versioned S3 bucket for artifacts;
- a DynamoDB provisioned mission projection at 5 RCU/5 WCU, without paid
  point-in-time recovery;
- one-week CloudWatch log groups; and
- least-purpose task roles that read the existing Temporal Cloud secret.

The App Runner service launches the one-shot coordinator and reads the
DynamoDB live projection. Its min/max size is 1 at 0.25 vCPU/0.5 GB. Agent and
coordinator compute remains scale-to-zero.

The POC uses public IPs solely to avoid the fixed NAT Gateway cost. Tasks accept
no inbound traffic. A production customer environment should use private
subnets plus VPC endpoints and controlled egress.

## Fresh-account bootstrap

The stack separates the infrastructure foundation from App Runner creation
because a new ECR repository cannot serve an application image until that image
has been published.

Prerequisites:

- AWS CDK is bootstrapped in the target account and Region.
- Secrets Manager contains `convoy/dev/temporal-cloud` with `namespace`,
  `endpoint`, and `apiKey` JSON fields.
- SSM Parameter Store contains the Standard SecureString
  `/convoy/poc/app-basic-auth` with a `user:password` value.

From `infra/cdk`, deploy the foundation:

```bash
pnpm install --frozen-lockfile
pnpm run deploy:foundation
```

Publish `poc-latest` using the commands below. Build and publish the root
application Dockerfile as `app-poc-latest`; on an ARM development machine, use
an amd64 CodeBuild builder rather than QEMU for the Next.js build. Then deploy
the complete POC:

```bash
pnpm run deploy:poc
```

Use `deploy:poc` for later updates to an environment that already has App
Runner. Running `deploy:foundation` by itself against that environment would
intentionally remove the optional App Runner resources.

## Rebuild and publish

From the repository root:

```bash
CONVOY_REPOSITORY_URI="$(
  aws cloudformation describe-stacks \
    --profile convoy-dev \
    --region us-west-2 \
    --stack-name ConvoyPocStack \
    --query "Stacks[0].Outputs[?OutputKey=='ImageRepositoryUri'].OutputValue" \
    --output text
)"

docker build --platform linux/arm64 \
  -t "$CONVOY_REPOSITORY_URI:poc-latest" poc/recursive-agents

aws ecr get-login-password --profile convoy-dev --region us-west-2 |
  docker login --username AWS --password-stdin \
    "${CONVOY_REPOSITORY_URI%%/*}"

docker push "$CONVOY_REPOSITORY_URI:poc-latest"
```

The runtime image includes the system CA bundle required by Temporal Cloud's
native TLS client and runs as a non-root user with a read-only root filesystem.

## Launch a mission

Resolve the current stack outputs instead of pinning a task-definition
revision:

```bash
MISSION_ID="convoy-poc-$(date -u +%Y%m%dT%H%M%SZ)"
CONVOY_CLUSTER_ARN="$(aws cloudformation describe-stacks --profile convoy-dev --region us-west-2 --stack-name ConvoyPocStack --query "Stacks[0].Outputs[?OutputKey=='ClusterArn'].OutputValue" --output text)"
CONVOY_COORDINATOR_TASK="$(aws cloudformation describe-stacks --profile convoy-dev --region us-west-2 --stack-name ConvoyPocStack --query "Stacks[0].Outputs[?OutputKey=='CoordinatorTaskDefinitionArn'].OutputValue" --output text)"
CONVOY_SUBNET_IDS="$(aws cloudformation describe-stacks --profile convoy-dev --region us-west-2 --stack-name ConvoyPocStack --query "Stacks[0].Outputs[?OutputKey=='RunnerSubnetIds'].OutputValue" --output text)"
CONVOY_SECURITY_GROUP="$(aws cloudformation describe-stacks --profile convoy-dev --region us-west-2 --stack-name ConvoyPocStack --query "Stacks[0].Outputs[?OutputKey=='RunnerSecurityGroupId'].OutputValue" --output text)"

aws ecs run-task \
  --profile convoy-dev \
  --region us-west-2 \
  --cluster "$CONVOY_CLUSTER_ARN" \
  --task-definition "$CONVOY_COORDINATOR_TASK" \
  --launch-type FARGATE \
  --network-configuration \
    "awsvpcConfiguration={subnets=[$CONVOY_SUBNET_IDS],securityGroups=[$CONVOY_SECURITY_GROUP],assignPublicIp=ENABLED}" \
  --overrides \
    "{\"containerOverrides\":[{\"name\":\"coordinator\",\"environment\":[{\"name\":\"MISSION_ID\",\"value\":\"$MISSION_ID\"}]}]}" \
  --tags key=Application,value=Convoy key=MissionId,value="$MISSION_ID"
```

The coordinator task is intentionally a one-shot Worker. It starts the durable
Temporal Mission Workflow, serves the mission's Workflow and Activity tasks,
and exits after the mission reaches a terminal state. Production uses the same
Workflow definitions behind an always-available Worker pool; very large trees
are sharded across supervisor Workflows and Continue-As-New boundaries.

## Expected result

The tree shape is model-decided, not fixed: each episode proposes up to
`MAX_FANOUT` children when decomposition helps, so episode count varies between
runs. What should hold every time is the envelope:

- no episode deeper than `MAX_DEPTH`, and no more than `maxTotalAgents` overall;
- never more than `maxParallelAgents` active slot leases at once;
- one DynamoDB record per episode plus the mission, all `COMPLETED`;
- one S3 artifact per episode plus the mission summary;
- coordinator exit code `0`; and
- zero running ECS tasks afterward.

Check a run against those bounds rather than an episode count. A coordinator
that exits before creating agent work should also leave zero running tasks —
failure containment is part of the contract, not just the happy path.
