# DigitalOcean control plane → AWS execution plane

The hosted Convoy application runs on DigitalOcean App Platform. It owns
accounts, sessions, workspaces, and the durable product-side record for each
mission in the attached PostgreSQL database. Mission execution remains in AWS:
the authenticated mission API launches the existing one-shot ECS coordinator,
which starts the Temporal workflow and projects live agent state into DynamoDB.

## Required DigitalOcean variables

Set these on the `convoy` web-service component, not globally:

```text
CONVOY_ACCOUNTS_ENABLED=1
CONVOY_CLOUD_RUNTIME=1
CONVOY_SESSION_SECRET=<strong random secret>
AWS_REGION=us-west-2
AWS_ACCESS_KEY_ID=<restricted control-plane principal>
AWS_SECRET_ACCESS_KEY=<restricted control-plane principal>
ECS_CLUSTER_ARN=<ConvoyPocStack ClusterArn>
COORDINATOR_TASK_DEFINITION_ARN=<ConvoyPocStack CoordinatorTaskDefinitionArn>
AGENT_SUBNET_IDS=<ConvoyPocStack RunnerSubnetIds>
AGENT_SECURITY_GROUP_ID=<ConvoyPocStack RunnerSecurityGroupId>
MISSION_TABLE_NAME=<ConvoyPocStack MissionTableName>
```

`DATABASE_URL` is supplied by the attached DigitalOcean PostgreSQL component.
The application automatically creates tables prefixed with `convoy_` on first
use. The existing demo JSON store remains separate.

## AWS principal permissions

The DigitalOcean component must use a dedicated IAM principal. It needs only:

- `ecs:RunTask` on `convoy-poc-coordinator:*`;
- `ecs:TagResource` for tasks launched by the control plane;
- `iam:PassRole` for the coordinator task and execution roles; and
- `dynamodb:GetItem`, `dynamodb:Query`, and `dynamodb:DescribeTable` on the
  mission projection table.

It must not receive the Temporal secret, agent task role, artifact-bucket write
access, or broad AWS administration permissions.

## Request lifecycle

```text
register/login
  → signed HTTP-only session
  → create workspace + owner membership in Postgres
  → create governed mission envelope in Postgres
  → DigitalOcean service calls ECS RunTask
  → coordinator starts Temporal MissionWorkflow
  → Fargate agent episodes project state to DynamoDB
  → mission detail API reads projection and updates Postgres terminal state
```

The POC uses an IAM access key because DigitalOcean does not receive an AWS
instance role. Rotate it after the demo. The production path should replace it
with short-lived cross-cloud federation.
