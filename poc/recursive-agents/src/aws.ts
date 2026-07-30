import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient } from "@aws-sdk/lib-dynamodb";
import { S3Client } from "@aws-sdk/client-s3";
import {
  GetSecretValueCommand,
  SecretsManagerClient,
} from "@aws-sdk/client-secrets-manager";

export const documentClient = DynamoDBDocumentClient.from(
  new DynamoDBClient({}),
  {
    marshallOptions: { removeUndefinedValues: true },
  },
);
export const s3Client = new S3Client({});

export interface TemporalConnectionConfig {
  namespace: string;
  endpoint: string;
  apiKey: string;
}

export async function loadTemporalConfig(): Promise<TemporalConnectionConfig> {
  const environmentConfig: TemporalConnectionConfig = {
    namespace: process.env.TEMPORAL_NAMESPACE ?? "",
    endpoint: process.env.TEMPORAL_ENDPOINT ?? "",
    apiKey: process.env.TEMPORAL_API_KEY ?? "",
  };
  if (
    environmentConfig.namespace &&
    environmentConfig.endpoint &&
    environmentConfig.apiKey
  ) {
    return environmentConfig;
  }

  const secretId = process.env.TEMPORAL_SECRET_ID;
  if (!secretId) {
    throw new Error(
      "Provide TEMPORAL_NAMESPACE, TEMPORAL_ENDPOINT, and TEMPORAL_API_KEY, or TEMPORAL_SECRET_ID",
    );
  }

  const response = await new SecretsManagerClient({}).send(
    new GetSecretValueCommand({ SecretId: secretId }),
  );
  if (!response.SecretString) {
    throw new Error(`Secret ${secretId} did not contain SecretString`);
  }

  const parsed = JSON.parse(response.SecretString) as TemporalConnectionConfig;
  if (!parsed.namespace || !parsed.endpoint || !parsed.apiKey) {
    throw new Error(`Secret ${secretId} is missing Temporal connection fields`);
  }
  return parsed;
}
