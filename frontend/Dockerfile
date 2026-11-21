# ----------------------------------------------------------------- builder
FROM node:22-alpine AS builder

WORKDIR /app
COPY package.json ./
# no lockfile is committed, so install resolves the ranges in package.json
RUN npm install --no-audit --no-fund

COPY . .
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# ----------------------------------------------------------------- runtime
FROM nginx:1.27-alpine AS runtime

ENV API_BASE_URL=""
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
# nginx runs every script in this folder before starting
COPY docker-entrypoint.sh /docker-entrypoint.d/40-app-config.sh
RUN chmod +x /docker-entrypoint.d/40-app-config.sh

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=4s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1
