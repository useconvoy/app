{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListTenantEnvPrefixOnly",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": ["${bucket_arn}"],
      "Condition": {
        "StringLike": {
          "s3:prefix": ["${tenant_id}/${env}/*"]
        }
      }
    },
    {
      "Sid": "ObjectsWithinTenantEnvPrefixOnly",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": ["${bucket_arn}/${tenant_id}/${env}/*"]
    },
    {
      "Sid": "ArtifactCrypto",
      "Effect": "Allow",
      "Action": ["kms:Decrypt", "kms:GenerateDataKey"],
      "Resource": ["${kms_key_arn}"]
    }
  ]
}
