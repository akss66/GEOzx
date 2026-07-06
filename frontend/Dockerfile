# DyFlow 前端镜像：Node 构建 → nginx 静态托管 + /api、/ws 反代后端。
FROM node:24-alpine AS build
WORKDIR /app
ENV COREPACK_NPM_REGISTRY=https://registry.npmmirror.com
RUN corepack enable
RUN pnpm config set registry https://registry.npmmirror.com

COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml ./
RUN pnpm install

COPY . .
RUN pnpm build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
