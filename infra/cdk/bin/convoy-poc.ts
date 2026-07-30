#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { ConvoyPocStack } from "../lib/convoy-poc-stack";

const app = new cdk.App();
const region = process.env.CONVOY_AWS_REGION ?? "us-west-2";

new ConvoyPocStack(app, "ConvoyPocStack", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region,
  },
  description:
    "Minimal Convoy recursive-agent POC: Temporal Cloud, ECS Fargate, S3, and DynamoDB",
});
