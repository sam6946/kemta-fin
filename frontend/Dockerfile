# --- Développement : Vite avec HMR -------------------------------------------
FROM node:22-alpine AS dev
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]

# --- Production : build statique servi par Nginx ------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Production (HTTPS) : TLS, HSTS, CSP, limitation de débit, médias protégés (X-Accel-Redirect).
FROM nginx:1.27-alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.prod.conf /etc/nginx/conf.d/default.conf
COPY proxy_api.conf /etc/nginx/proxy_api.conf
EXPOSE 80 443

# Variante HTTP seule : uniquement derrière un terminateur TLS externe (load balancer géré).
FROM nginx:1.27-alpine AS prod-http
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
