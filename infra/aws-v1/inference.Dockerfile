# BASE_IMAGE is the qualified scripted reference image pinned by registry digest.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
COPY --chown=10001:10001 infra/aws-v1/runtime/inference.py /app/aws-runtime/inference.py
CMD ["python", "/app/aws-runtime/inference.py"]
