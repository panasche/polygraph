# syntax=docker/dockerfile:1.7
# Polygraph web: landing page + dashboard (Next.js standalone output).

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
# Baked in at build time: next.config.ts reads API_ORIGIN for the /api rewrite, and
# prerendered pages (metadata, sitemap, privacy notice, footer) read the rest.
ARG API_ORIGIN=http://api:8000
ARG PUBLIC_BASE_URL=https://polygraphmcp.dev
ARG PRIVACY_VERSION=2026-10-13
ARG GITHUB_APP_SLUG=polygraph-for-mcp
ARG HOSTING=aws-mumbai
ARG SOURCE_URL=https://github.com/polygraph-dev/polygraph
ARG SECURITY_EMAIL=security@polygraphmcp.dev
ARG GRIEVANCE_EMAIL=grievance@polygraphmcp.dev
# Empty means the site has no status page.
ARG STATUS_URL=https://status.polygraphmcp.dev
ENV NEXT_TELEMETRY_DISABLED=1 \
    API_ORIGIN=$API_ORIGIN \
    PUBLIC_BASE_URL=$PUBLIC_BASE_URL \
    PRIVACY_VERSION=$PRIVACY_VERSION \
    GITHUB_APP_SLUG=$GITHUB_APP_SLUG \
    HOSTING=$HOSTING \
    SOURCE_URL=$SOURCE_URL \
    SECURITY_EMAIL=$SECURITY_EMAIL \
    GRIEVANCE_EMAIL=$GRIEVANCE_EMAIL \
    NEXT_PUBLIC_STATUS_URL=$STATUS_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S -g 1001 web && adduser -S -u 1001 -G web web
COPY --from=build --chown=web:web /app/.next/standalone ./
COPY --from=build --chown=web:web /app/.next/static ./.next/static
COPY --from=build --chown=web:web /app/public ./public
# The only writable path under ECS's read-only root file system.
RUN mkdir -p /app/.next/cache && chown web:web /app/.next/cache
VOLUME ["/app/.next/cache"]
USER web
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3000/.well-known/security.txt >/dev/null || exit 1
CMD ["node", "server.js"]
