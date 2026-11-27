# syntax=docker/dockerfile:1.7
# Azgaar's Fantasy Map Generator (MIT, https://github.com/Azgaar/Fantasy-Map-Generator), self-hosted on the LAN.
# Built from a pinned tag. `--mode electron` is the desktop renderer's build: a relative base path (served from /)
# and no Google Analytics or PWA tags; the app still runs as the web version (Electron is detected at runtime).
FROM node:24-alpine AS build
ARG FMG_REF=v1.157.0
ADD --keep-git-dir=false https://github.com/Azgaar/Fantasy-Map-Generator.git#${FMG_REF} /app
WORKDIR /app
RUN npm ci --no-audit --no-fund && npx vite build --mode electron

FROM nginx:1.27-alpine
COPY --from=build /app/dist-electron/renderer /usr/share/nginx/html
COPY --from=build /app/LICENSE /usr/share/nginx/html/LICENSE.txt
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
