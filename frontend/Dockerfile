FROM node:24.15.0-alpine AS build
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.28-alpine
ARG WEB_RELEASE_SHA=local-web-test
COPY --from=build /build/dist /usr/share/nginx/html
RUN printf '{"revision":"%s"}\n' "$WEB_RELEASE_SHA" > /usr/share/nginx/html/release.json
COPY deploy/web.conf /etc/nginx/nginx.conf
COPY deploy/web-api.conf /etc/nginx/web-api.conf
# Nginx runs without root. TLS material must be readable by this UID.
USER 101:101
ENTRYPOINT ["nginx", "-g", "daemon off;"]
