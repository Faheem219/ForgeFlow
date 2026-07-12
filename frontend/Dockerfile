# ForgeFlow PWA (dev image). Source is bind-mounted by docker-compose for hot reload.
FROM node:20-slim

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    COREPACK_INTEGRITY_KEYS=0
RUN corepack enable

WORKDIR /app

COPY package.json pnpm-lock.yaml* ./
RUN corepack pnpm install

COPY . .

EXPOSE 5173
CMD ["corepack", "pnpm", "dev", "--host"]
