# SNOWKAP ESG Frontend — Multi-stage build
# Stage 1: Build React app with Vite
# Stage 2: Serve via Nginx

# --- Build stage ---
FROM node:20-alpine AS build

WORKDIR /app

COPY client/package.json client/package-lock.json* ./
RUN npm ci

COPY client/ ./
RUN npm run build

# --- Production stage ---
FROM nginx:1.27-alpine

# Copy built React app to nginx html directory
COPY --from=build /app/dist /usr/share/nginx/html

# Copy nginx config
COPY docker/nginx/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
