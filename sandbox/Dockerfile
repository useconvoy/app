FROM node:22-alpine

WORKDIR /app
COPY . .

ENV PORT=8790
ENV SANDBOX_RUNTIME_ROOT=/var/lib/convoy-sandbox
EXPOSE 8790

CMD ["node", "apps/sandbox-control-plane/src/server.mjs"]
