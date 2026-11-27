# syntax=docker/dockerfile:1
# Build context: the REPO ROOT (the image bundles deploy/Caddyfile next to the built SPA).
#   docker build -f frontend/Dockerfile .
# Output: Caddy serving the static SPA and proxying /api/* to the api service.

FROM node:24-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build

FROM caddy:2.11 AS runtime
# Non-root. Binding :80/:443 without privileges is granted by the container's own sysctl
# (net.ipv4.ip_unprivileged_port_start=0 in compose.yml), not by file capabilities.
RUN addgroup -S -g 10001 nexus && adduser -S -u 10001 -G nexus -h /srv nexus \
 && mkdir -p /data /config && chown -R nexus:nexus /data /config
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build --chown=nexus:nexus /app/dist /srv
USER nexus
