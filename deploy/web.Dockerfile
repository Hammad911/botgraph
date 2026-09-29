# BotGraph analyst console (Next.js standalone server).
#   docker build -f deploy/web.Dockerfile -t botgraph-web .
#
# REST calls are rewritten to BOTGRAPH_API_URL (fixed at build time). Leave
# NEXT_PUBLIC_BOTGRAPH_WS_URL empty when an ingress serves /api on the console's origin (the
# Helm chart does): the WebSocket is then same-origin.

FROM node:22-alpine AS build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
ARG BOTGRAPH_API_URL=http://api:8000
ARG NEXT_PUBLIC_BOTGRAPH_WS_URL=
ENV BOTGRAPH_API_URL=$BOTGRAPH_API_URL \
    NEXT_PUBLIC_BOTGRAPH_WS_URL=$NEXT_PUBLIC_BOTGRAPH_WS_URL \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_OUTPUT=standalone
RUN npm run build

FROM node:22-alpine
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /web
COPY --from=build --chown=node:node /web/.next/standalone ./
COPY --from=build --chown=node:node /web/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
