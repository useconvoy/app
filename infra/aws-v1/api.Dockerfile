# BASE_IMAGE is a previously qualified API image pinned by registry digest.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY --chown=10001:10001 infra/aws-v1/runtime/entrypoint.py infra/aws-v1/runtime/bootstrap.py infra/aws-v1/runtime/hosted.py infra/aws-v1/runtime/rds-ca.pem /app/aws-runtime/
COPY --chown=10001:10001 infra/runtime/execution_keys.py /app/runtime/execution_keys.py
# Existing non-root API image/user is preserved.
CMD ["python", "/app/aws-runtime/entrypoint.py", "api"]
