# Costed staging example: US East, ARM CPU

Price check: **September 27, 2026**. Example only: `us-east-1` (N. Virginia),
Linux ARM64, 730 hours/month, on-demand, one continuously running replica of each
service, single-AZ RDS PostgreSQL 17.11, two ALBs, one NAT, no GPU. Account,
region, required active hours and budget still need the user's decision before
apply. USD, before tax; no credits, free-tier benefits, commitments or discounts.

[Raw rate references](../../infra/aws-v1/pricing-reference.json) record the
selected public AWS offer SKUs and publication dates. Re-price the chosen region
and review an AWS plan before creating resources. This is an estimate, not an
AWS bill or a hard spending limit.

## Always-on resources

| Resource | Declared quantity and rate | Monthly estimate |
| --- | --- | ---: |
| Fargate ARM CPU/memory | 2.25 vCPU + 4.5 GiB, $0.03238/vCPU-hour + $0.00356/GiB-hour, 730h | $64.88 |
| RDS PostgreSQL db.t4g.small | One single-AZ instance, $0.032/hour, 730h | $23.36 |
| RDS gp3 | 20 GB, $0.115/GB-month | $2.30 |
| Two Application Load Balancers | 2 × $0.0225/hour × 730h, before traffic | $32.85 |
| One NAT gateway | $0.045/hour × 730h, before traffic | $32.85 |
| Public IPv4 | Minimum four ALB addresses + one NAT address, $0.005/address-hour | $18.25 |
| Secrets Manager | Five app secret containers + one RDS-managed master, $0.40/secret-month | $2.40 |
| **Fixed subtotal** | Service count one, including evaluation process | **$176.89** |

The Fargate quantity is web .25/.5, API .5/1, scheduler .25/.5, evaluations
.25/.5 and inference 1/2 (vCPU/GiB). Each task uses the included 20 GiB ephemeral
storage; there is no extra ephemeral-storage allocation. One-off bootstrap and
migration tasks are billed only while running, including image pull time.
[AWS Fargate prices](https://aws.amazon.com/fargate/pricing/),
[regional ARM offer data](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/current/us-east-1/index.json)

RDS rates are the published single-AZ PostgreSQL `db.t4g.small` and GP3 SKUs,
not EC2 instance/EBS pricing. Automated backup storage up to the provisioned
DB storage allowance is assumed included for the active database. T4g unlimited
CPU surplus, snapshots beyond allowance, extended-support charges if a future
engine enters that period, and restore copies are additional.
[RDS PostgreSQL pricing](https://aws.amazon.com/rds/postgresql/pricing/),
[regional RDS offer data](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonRDS/current/us-east-1/index.json)

ALB capacity and IPv4 address count can grow with traffic. The four ALB addresses
are a low-load estimate across two AZs, not a configured billing cap. NAT remains
billable while no task is running. S3 gateway endpoints have no hourly fee and
avoid NAT processing for same-region ECR/S3 layer access.
[Load balancer pricing](https://aws.amazon.com/elasticloadbalancing/pricing/),
[VPC/NAT/IPv4 pricing](https://aws.amazon.com/vpc/pricing/)

## Small demonstration workload allowance

| Variable item | Example use, excluding free tier | Monthly estimate |
| --- | --- | ---: |
| ALB capacity | 1 average LCU per ALB, 2 × .008 × 730 | $11.68 |
| NAT processing | 10 GB × .045 | $0.45 |
| Regional network allowance | 20 GB charged network legs × .01 (web to public API ALB) | $0.20 |
| CloudWatch logs | 5 GB ingestion × .50 + 1 GB average retained × .03 | $2.53 |
| ECR retained images | 10 GB across qualified/rollback images × .10 | $1.00 |
| Existing public DNS zone allocation | One standard zone | $0.50 |
| Internet egress allowance | 10 GB at $0.09/GB before any shared free allowance | $0.90 |
| Secret API calls | 10,000 requests × .05 | $0.05 |
| **Example total** | Fixed subtotal plus the above | **about $194/month** |

Use **$190–210/month** for this specifically declared low-traffic always-on
configuration, then add a deliberate margin; a $250–300 warning threshold is an
example for discussion, not approved spending. Large observations, video,
public abuse, unbounded logs, extra images, DB CPU credits, cross-AZ transfer
or a GPU can materially exceed it. DNS queries, backend state storage/requests,
short migration tasks and budget notification delivery are small but not zero.
No ACM private CA, interface endpoints, S3 artifact service, customer GPU,
EFS, WAF, load tests or paid support plan is included.
[CloudWatch](https://aws.amazon.com/cloudwatch/pricing/),
[ECR](https://aws.amazon.com/ecr/pricing/),
[Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/),
[Route53](https://aws.amazon.com/route53/pricing/),
[EC2 data transfer](https://aws.amazon.com/ec2/pricing/on-demand/)

Network usage can be recalculated as `$0.045 × NAT_GB + $0.09 × Internet_GB
+ $0.01 × regional_charged_leg_GB`, before tier/allowance changes. Web reaches
the API's public HTTPS ALB through the NAT/Internet gateway, so its regional
transfer is additional to NAT processing. ALB-to-target cross-zone traffic
within this VPC is free. Tasks, NAT and RDS currently share the first AZ; moving
tasks/DB/NAT across AZs introduces additional regional charged legs. Review
actual routing and billed byte dimensions when changing placement, rather than
applying the ALB cross-zone exemption to every connection.
[AWS ALB transfer scenarios](https://aws.amazon.com/blogs/networking-and-content-delivery/exploring-data-transfer-costs-for-classic-and-application-load-balancers/),
[ALB cross-zone pricing](https://aws.amazon.com/elasticloadbalancing/faqs/)

## Idle and teardown costs

Setting `services_enabled=false` stops the approximately **$64.88/month**
Fargate portion. It leaves approximately **$112/month fixed infrastructure**
plus variable storage/requests: RDS, both ALBs, the NAT and five minimum public
IPv4 addresses continue billing. Running services for 8 hours on 22 weekdays
(176 hours) instead of 730 costs about $15.64 for Fargate but still roughly
$128 fixed total; scaling tasks down alone does not make staging inexpensive.

Terraform destroy removes this configuration's running infrastructure after
retaining its final RDS snapshot. Snapshots, pre-existing ECR images, the state
bucket/DNS zone/certificates need explicit retention decisions. A retained 20 GB
RDS snapshot at $0.095/GB-month is about $1.90/month after the active DB allowance
is gone; existing ECR/state storage also remains billable. Secrets scheduled for
deletion remain recoverable for seven days but do not incur storage charges
during that period. [Secret deletion](https://docs.aws.amazon.com/secretsmanager/latest/userguide/manage_delete-secret.html) Repeatedly stopping RDS is not permanent shutdown: AWS
restarts a stopped DB after seven days.
[RDS stopping behavior](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_StopInstance.html)

The created budget notification monitors the **whole selected AWS account**, so
an existing account's other workloads also count. It does not prevent launches,
turn resources off or guarantee timely interruption of spend. A dedicated
staging account makes attribution and notifications clearer. Infrastructure
teardown, capacity limits and active operator review remain the cost controls.
[AWS Budgets](https://aws.amazon.com/aws-cost-management/aws-budgets/)
