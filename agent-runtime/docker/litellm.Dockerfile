# Multi-architecture LiteLLM database image, pinned to the v1.95.0 manifest.
# The upstream image includes Prisma and its native engines for both amd64 and
# arm64; keeping those artifacts upstream avoids host-architecture wheel caches
# leaking into cloud builds.
FROM ghcr.io/berriai/litellm-database@sha256:af7ff0444278be40f7615764d1562ac557df6103cba052e886915a7e1e944c27

ENV CHECKPOINT_DISABLE=1 \
    PRISMA_HIDE_UPDATE_MESSAGE=1

COPY agent-runtime/docker/litellm/config.yaml /srv/litellm-config.yaml

EXPOSE 4000
HEALTHCHECK --interval=3s --timeout=3s --start-period=90s --retries=40 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness')"

ENTRYPOINT ["litellm"]
CMD ["--config", "/srv/litellm-config.yaml", "--host", "0.0.0.0", "--port", "4000", "--use_prisma_db_push"]
