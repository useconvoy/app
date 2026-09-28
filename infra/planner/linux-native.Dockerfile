# Build tooling only. Source is supplied separately as an exact git archive;
# weights, private assets and the source checkout never enter the image context.
FROM debian:bookworm-slim@sha256:0c8bbb8e987a035fe1d9704eb2e571b7e9a836e1caa46345290674b45b69e417
RUN rm /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20260927T000000Z bookworm main' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20260927T000000Z bookworm-security main' \
      > /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake ninja-build python3 binutils file ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 builder && useradd --uid 10001 --gid builder --create-home builder \
    && mkdir /work && chown builder:builder /work
COPY package_linux_native.py /opt/package_linux_native.py
USER builder
WORKDIR /work
ENV LC_ALL=C TZ=UTC
ENTRYPOINT ["python3", "/opt/package_linux_native.py"]
