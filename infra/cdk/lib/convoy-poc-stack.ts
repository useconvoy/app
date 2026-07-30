import {
  CfnOutput,
  RemovalPolicy,
  Stack,
  StackProps,
  Tags,
} from "aws-cdk-lib";
import { Construct } from "constructs";
import * as apprunner from "aws-cdk-lib/aws-apprunner";
import * as budgets from "aws-cdk-lib/aws-budgets";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";

export class ConvoyPocStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    Tags.of(this).add("Application", "Convoy");
    Tags.of(this).add("Environment", "poc");
    Tags.of(this).add("ManagedBy", "CDK");

    // Public-only subnets avoid NAT Gateway fixed cost for this disposable POC.
    // Tasks receive public IPs but accept no inbound traffic.
    const vpc = new ec2.Vpc(this, "Vpc", {
      vpcName: "convoy-poc",
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: "runner",
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
      ],
    });

    const runnerSecurityGroup = new ec2.SecurityGroup(
      this,
      "RunnerSecurityGroup",
      {
        vpc,
        description:
          "No-ingress security group for outbound-only Convoy POC tasks",
        allowAllOutbound: true,
      },
    );

    const artifactBucket = new s3.Bucket(this, "ArtifactBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
      autoDeleteObjects: true,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const missionTable = new dynamodb.Table(this, "MissionTable", {
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PROVISIONED,
      readCapacity: 5,
      writeCapacity: 5,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const imageRepository = new ecr.Repository(this, "ImageRepository", {
      repositoryName: "convoy-poc",
      imageScanOnPush: true,
      imageTagMutability: ecr.TagMutability.MUTABLE,
      emptyOnDelete: true,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const cluster = new ecs.Cluster(this, "Cluster", {
      clusterName: "convoy-poc",
      vpc,
      containerInsightsV2: ecs.ContainerInsights.DISABLED,
    });

    const temporalSecret = secretsmanager.Secret.fromSecretNameV2(
      this,
      "TemporalSecret",
      "convoy/dev/temporal-cloud",
    );

    const agentLogGroup = new logs.LogGroup(this, "AgentLogGroup", {
      logGroupName: "/convoy/poc/agent",
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const coordinatorLogGroup = new logs.LogGroup(
      this,
      "CoordinatorLogGroup",
      {
        logGroupName: "/convoy/poc/coordinator",
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: RemovalPolicy.DESTROY,
      },
    );

    const runtimePlatform: ecs.RuntimePlatform = {
      cpuArchitecture: ecs.CpuArchitecture.ARM64,
      operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
    };

    const agentTaskDefinition = new ecs.FargateTaskDefinition(
      this,
      "AgentTaskDefinition",
      {
        family: "convoy-poc-agent",
        cpu: 256,
        memoryLimitMiB: 512,
        runtimePlatform,
      },
    );
    agentTaskDefinition.addVolume({ name: "tmp" });

    const agentContainer = agentTaskDefinition.addContainer("agent", {
      containerName: "agent",
      image: ecs.ContainerImage.fromEcrRepository(
        imageRepository,
        "poc-latest",
      ),
      essential: true,
      readonlyRootFilesystem: true,
      logging: ecs.LogDrivers.awsLogs({
        logGroup: agentLogGroup,
        streamPrefix: "episode",
      }),
      environment: {
        MODE: "agent",
        TEMPORAL_SECRET_ID: temporalSecret.secretName,
        MISSION_TABLE_NAME: missionTable.tableName,
        ARTIFACT_BUCKET_NAME: artifactBucket.bucketName,
      },
    });
    agentContainer.addMountPoints({
      sourceVolume: "tmp",
      containerPath: "/tmp",
      readOnly: false,
    });

    temporalSecret.grantRead(agentTaskDefinition.taskRole);
    missionTable.grantReadWriteData(agentTaskDefinition.taskRole);
    artifactBucket.grantReadWrite(agentTaskDefinition.taskRole);

    const coordinatorTaskDefinition = new ecs.FargateTaskDefinition(
      this,
      "CoordinatorTaskDefinition",
      {
        family: "convoy-poc-coordinator",
        cpu: 512,
        memoryLimitMiB: 1024,
        runtimePlatform,
      },
    );
    coordinatorTaskDefinition.addVolume({ name: "tmp" });

    const coordinatorContainer = coordinatorTaskDefinition.addContainer(
      "coordinator",
      {
        containerName: "coordinator",
        image: ecs.ContainerImage.fromEcrRepository(
          imageRepository,
          "poc-latest",
        ),
        essential: true,
        readonlyRootFilesystem: true,
        logging: ecs.LogDrivers.awsLogs({
          logGroup: coordinatorLogGroup,
          streamPrefix: "mission",
        }),
        environment: {
          MODE: "coordinator",
          TEMPORAL_SECRET_ID: temporalSecret.secretName,
          MISSION_TABLE_NAME: missionTable.tableName,
          ARTIFACT_BUCKET_NAME: artifactBucket.bucketName,
          ECS_CLUSTER_ARN: cluster.clusterArn,
          AGENT_TASK_DEFINITION_ARN: agentTaskDefinition.taskDefinitionArn,
          AGENT_SUBNET_IDS: vpc.publicSubnets
            .map((subnet) => subnet.subnetId)
            .join(","),
          AGENT_SECURITY_GROUP_ID: runnerSecurityGroup.securityGroupId,
          TEMPORAL_TASK_QUEUE: "convoy-poc",
          DEFAULT_MAX_FANOUT: "3",
          DEFAULT_MAX_DEPTH: "2",
          DEFAULT_MAX_TOTAL_AGENTS: "13",
          DEFAULT_MAX_PARALLEL_AGENTS: "6",
          DEFAULT_MAX_COST_CENTS: "100",
        },
      },
    );
    coordinatorContainer.addMountPoints({
      sourceVolume: "tmp",
      containerPath: "/tmp",
      readOnly: false,
    });

    temporalSecret.grantRead(coordinatorTaskDefinition.taskRole);
    missionTable.grantReadWriteData(coordinatorTaskDefinition.taskRole);
    artifactBucket.grantReadWrite(coordinatorTaskDefinition.taskRole);

    coordinatorTaskDefinition.taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["ecs:RunTask"],
        resources: [agentTaskDefinition.taskDefinitionArn],
      }),
    );
    coordinatorTaskDefinition.taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["ecs:TagResource"],
        resources: ["*"],
      }),
    );
    coordinatorTaskDefinition.taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["iam:PassRole"],
        resources: [
          agentTaskDefinition.taskRole.roleArn,
          agentTaskDefinition.executionRole!.roleArn,
        ],
      }),
    );

    // A fresh account needs the ECR repositories before it can publish the
    // application image. Deploy once without this context, publish both image
    // tags, then deploy with `-c deployApp=true`.
    const deployApp = this.node.tryGetContext("deployApp") === "true";
    let appService: apprunner.CfnService | undefined;

    if (deployApp) {
      const appRunnerEcrRole = new iam.Role(this, "AppRunnerEcrRole", {
      assumedBy: new iam.ServicePrincipal("build.apprunner.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSAppRunnerServicePolicyForECRAccess",
        ),
      ],
    });

      const appRunnerInstanceRole = new iam.Role(
      this,
      "AppRunnerInstanceRole",
      {
        assumedBy: new iam.ServicePrincipal("tasks.apprunner.amazonaws.com"),
      },
    );
    missionTable.grantReadData(appRunnerInstanceRole);
    appRunnerInstanceRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["ecs:RunTask"],
        resources: [coordinatorTaskDefinition.taskDefinitionArn],
      }),
    );
    appRunnerInstanceRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["ecs:TagResource"],
        resources: ["*"],
      }),
    );
    appRunnerInstanceRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["iam:PassRole"],
        resources: [
          coordinatorTaskDefinition.taskRole.roleArn,
          coordinatorTaskDefinition.executionRole!.roleArn,
        ],
      }),
    );
    appRunnerInstanceRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["ssm:GetParameters"],
        resources: [
          `arn:${this.partition}:ssm:${this.region}:${this.account}:parameter/convoy/poc/app-basic-auth`,
        ],
      }),
    );

      const appAutoScaling = new apprunner.CfnAutoScalingConfiguration(
      this,
      "AppAutoScaling",
      {
        autoScalingConfigurationName: "convoy-poc-one-instance",
        minSize: 1,
        maxSize: 1,
        maxConcurrency: 25,
      },
    );

      appService = new apprunner.CfnService(this, "AppService", {
      serviceName: "convoy-poc-app",
      autoScalingConfigurationArn:
        appAutoScaling.attrAutoScalingConfigurationArn,
      sourceConfiguration: {
        autoDeploymentsEnabled: false,
        authenticationConfiguration: {
          accessRoleArn: appRunnerEcrRole.roleArn,
        },
        imageRepository: {
          imageIdentifier: `${imageRepository.repositoryUri}:app-poc-latest`,
          imageRepositoryType: "ECR",
          imageConfiguration: {
            port: "3000",
            runtimeEnvironmentVariables: [
              { name: "NODE_ENV", value: "production" },
              { name: "CONVOY_CLOUD_RUNTIME", value: "1" },
              { name: "CONVOY_DATA_DIR", value: "/data" },
              { name: "ECS_CLUSTER_ARN", value: cluster.clusterArn },
              {
                name: "COORDINATOR_TASK_DEFINITION_ARN",
                value: coordinatorTaskDefinition.taskDefinitionArn,
              },
              {
                name: "AGENT_SUBNET_IDS",
                value: vpc.publicSubnets
                  .map((subnet) => subnet.subnetId)
                  .join(","),
              },
              {
                name: "AGENT_SECURITY_GROUP_ID",
                value: runnerSecurityGroup.securityGroupId,
              },
              { name: "MISSION_TABLE_NAME", value: missionTable.tableName },
            ],
            runtimeEnvironmentSecrets: [
              {
                name: "CONVOY_BASIC_AUTH",
                value: `arn:${this.partition}:ssm:${this.region}:${this.account}:parameter/convoy/poc/app-basic-auth`,
              },
            ],
          },
        },
      },
      instanceConfiguration: {
        cpu: "0.25 vCPU",
        memory: "0.5 GB",
        instanceRoleArn: appRunnerInstanceRole.roleArn,
      },
      healthCheckConfiguration: {
        protocol: "HTTP",
        path: "/api/health",
        interval: 10,
        timeout: 5,
        healthyThreshold: 1,
        unhealthyThreshold: 5,
      },
      networkConfiguration: {
        ingressConfiguration: { isPubliclyAccessible: true },
        egressConfiguration: { egressType: "DEFAULT" },
      },
    });
    }

    new budgets.CfnBudget(this, "MonthlyBudget", {
      budget: {
        budgetName: "ConvoyPocMonthlyBudget",
        budgetType: "COST",
        timeUnit: "MONTHLY",
        budgetLimit: { amount: 10, unit: "USD" },
      },
    });

    new CfnOutput(this, "ArtifactBucketName", {
      value: artifactBucket.bucketName,
    });
    new CfnOutput(this, "MissionTableName", {
      value: missionTable.tableName,
    });
    new CfnOutput(this, "ImageRepositoryUri", {
      value: imageRepository.repositoryUri,
    });
    new CfnOutput(this, "ClusterArn", { value: cluster.clusterArn });
    new CfnOutput(this, "CoordinatorTaskDefinitionArn", {
      value: coordinatorTaskDefinition.taskDefinitionArn,
    });
    new CfnOutput(this, "AgentTaskDefinitionArn", {
      value: agentTaskDefinition.taskDefinitionArn,
    });
    new CfnOutput(this, "RunnerSubnetIds", {
      value: vpc.publicSubnets.map((subnet) => subnet.subnetId).join(","),
    });
    new CfnOutput(this, "RunnerSecurityGroupId", {
      value: runnerSecurityGroup.securityGroupId,
    });
    new CfnOutput(this, "CoordinatorLogGroupName", {
      value: coordinatorLogGroup.logGroupName,
    });
    if (appService) {
      new CfnOutput(this, "AppRunnerServiceUrl", {
        value: appService.attrServiceUrl,
      });
    }
  }
}
