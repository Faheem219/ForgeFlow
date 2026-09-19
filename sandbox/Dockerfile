# ForgeFlow sandbox base image — where untrusted, generated apps run (D5, §4, §7).
#
# The control plane NEVER execs generated code; it runs only inside a container built from this
# image. Runtime isolation (no host network, dropped caps, cpu/mem/pid limits) is applied by the
# sandbox MANAGER (phase-11) at `docker run` time, not baked here — so the image stays reusable.
#
# Contents: Node 20 + a BAKED pnpm (see "package managers" below) + git + curl + Playwright chromium
# (shared, world-readable), a non-root `app` user (uid 1000), and a workspace at /workspace. No
# secrets are baked in.
FROM node:20-bookworm-slim

# Pinned so builds are reproducible; bump deliberately.
ARG PLAYWRIGHT_VERSION=1.48.2
# Keep in lockstep with `packageManager` in templates/app-skeleton/package.json — corepack resolves
# the version the generated app asks for, and offline only a *baked* version can be resolved.
ARG PNPM_VERSION=9.12.0
# (MONGO_VERSION is pinned beside its own layer further down, so bumping it rebuilds only that.)

ENV DEBIAN_FRONTEND=noninteractive \
    WORKSPACE=/workspace \
    # -- package managers -----------------------------------------------------------------
    # Corepack's cache is baked here (world-readable) instead of the default `$HOME/.cache`:
    # the container runs with a READ-ONLY root, so a first-use download under $HOME is exactly
    # the `ENOENT … mkdir '/home/app/.cache/node/corepack/v1'` that used to kill every single
    # `pnpm` invocation in the sandbox. Baking it also means no registry access is needed just
    # to *start* pnpm — the sandbox has no network by default.
    COREPACK_HOME=/opt/corepack \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    # A generated app whose `packageManager` field drifts from the baked pnpm must fall back to
    # it rather than trying (and failing, offline) to fetch its own.
    COREPACK_ENABLE_STRICT=0 \
    # -- Playwright -----------------------------------------------------------------------
    # Shared browser cache (Playwright's conventional location) + a fixed playwright install so
    # smoke/tooling can `require('playwright')` via NODE_PATH (CommonJS resolution).
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NODE_PATH=/opt/pw/node_modules

# Minimal, pinned-by-distro system packages. tini gives us a real PID 1 (signal forwarding + zombie
# reaping); ca-certificates for TLS; git/curl for the workspace + healthchecks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        # procps (ps/pgrep/pkill) + psmisc (fuser) let the preview reclaim its fixed ports and kill
        # a dev-server process TREE (phase-59). Without them a `pnpm` parent dies leaving its `vite`
        # child holding 5173, Vite slides to 5175, and the proxy serves nothing. A few hundred KB.
        procps \
        psmisc \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Bake pnpm into the image (build time is the only time there IS a network). `--activate` records
# it as corepack's lastKnownGood, so a bare `pnpm` in a workspace with no packageManager field
# still resolves. The COREPACK_ENABLE_NETWORK=0 checks are the regression guard: they fail the
# build if anything would still need to reach the registry to run pnpm — as root and as `app`.
RUN mkdir -p "${COREPACK_HOME}" \
    && corepack enable \
    && corepack prepare "pnpm@${PNPM_VERSION}" --activate \
    && chmod -R a+rX "${COREPACK_HOME}" \
    && COREPACK_ENABLE_NETWORK=0 pnpm --version

# Playwright + chromium and its OS dependencies (installed as root via --with-deps). Kept in a fixed
# location and made world-readable so the non-root `app` user can launch the browser at runtime.
RUN mkdir -p /opt/pw \
    && cd /opt/pw \
    && npm init -y >/dev/null 2>&1 \
    && npm install --no-save "playwright@${PLAYWRIGHT_VERSION}" \
    && npx playwright install --with-deps chromium \
    && chmod -R a+rX /opt/pw /ms-playwright

# The mongod generated-app tests run against (phase-65). mongodb-memory-server downloads one on
# first USE, and a test run has no network — which is how "unable to download mongod" ended a real
# repair session. So it is baked here, at the only time there IS a network, and the MONGOMS_* env
# above points the library at it. Only `bin/mongod` is kept. It binds 127.0.0.1 inside the container:
# this adds a binary, never a route. Official community tarballs, not `COPY --from=mongo:8.0`: that
# image is built on Ubuntu noble (glibc 2.39), which bookworm (2.36) cannot be relied on to load.
# MongoDB publishes no arm64 Debian build; the jammy one needs glibc >= 2.35. TARGETARCH is set by
# BuildKit; the legacy builder leaves it empty, hence the dpkg fallback.
# 8.0 is Atlas's default major version, so tests and the deployed database run the same server line.
# The checksums are MongoDB's published sha256 for the two tarballs; bump all three together.
ARG MONGO_VERSION=8.0.32
ARG MONGO_SHA256_AMD64=b61cda162c6592347b7503c683145304f74c900b1c10a395520c7c24d3f8445c
ARG MONGO_SHA256_ARM64=7411bc9efef53346a26e67241ca9db9926c30b7a056e2515068223b497682c81
ARG TARGETARCH
RUN set -eu; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "${arch}" in \
      amd64) tarball="mongodb-linux-x86_64-debian12-${MONGO_VERSION}"; sha="${MONGO_SHA256_AMD64}" ;; \
      arm64) tarball="mongodb-linux-aarch64-ubuntu2204-${MONGO_VERSION}"; sha="${MONGO_SHA256_ARM64}" ;; \
      *) echo "no MongoDB ${MONGO_VERSION} build for ${arch}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/mongodb.tgz "https://fastdl.mongodb.org/linux/${tarball}.tgz"; \
    echo "${sha}  /tmp/mongodb.tgz" | sha256sum -c -; \
    tar -xzf /tmp/mongodb.tgz -C /tmp "${tarball}/bin/mongod"; \
    install -m 0755 "/tmp/${tarball}/bin/mongod" /usr/local/bin/mongod; \
    rm -rf /tmp/mongodb.tgz "/tmp/${tarball}"; \
    mongod --version

# mongodb-memory-server(-core) uses the baked binary and NEVER downloads one: with no network a
# download can only fail, so it fails fast with a clear error rather than after a DNS timeout. The
# version matches the binary (no conflict warning; wiredTiger is chosen for >= 7.0).
# DISABLE_POSTINSTALL stops the non-core package fetching ~105 MB inside an install window.
ENV MONGOMS_SYSTEM_BINARY=/usr/local/bin/mongod \
    MONGOMS_VERSION=${MONGO_VERSION} \
    MONGOMS_RUNTIME_DOWNLOAD=false \
    MONGOMS_DISABLE_POSTINSTALL=1

# Non-root user at uid 1000. The base node image already owns uid 1000 (its `node` user); reclaim it
# for `app` so the workspace has a predictable, unprivileged owner. The cache dirs are pre-created
# (and owned) here so the per-project home volume the manager mounts over /home/app seeds with the
# right layout and ownership.
RUN userdel -r node 2>/dev/null || true \
    && useradd --uid 1000 --user-group --create-home --shell /bin/bash app \
    && mkdir -p "${WORKSPACE}" \
        /home/app/.cache \
        /home/app/.npm \
        /home/app/.local/share/pnpm/store \
    && chown -R app:app "${WORKSPACE}" /home/app \
    && su -s /bin/bash app -c 'COREPACK_ENABLE_NETWORK=0 pnpm --version' \
    && su -s /bin/bash app -c 'mongod --version >/dev/null'

# Declared after the user exists so the RUN steps above keep root's own HOME. /home/app is mounted
# from a writable per-project volume at runtime (phase-11 manager) — the read-only root makes every
# other $HOME write fail, and pnpm/npm/corepack all write under $HOME.
ENV HOME=/home/app \
    PNPM_HOME=/home/app/.local/share/pnpm
ENV PATH=/home/app/.local/share/pnpm:/usr/local/bin:$PATH

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR ${WORKSPACE}
USER app

# tini reaps zombies + forwards signals; entrypoint keeps the container alive for exec/preview and
# never auto-runs untrusted code.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD []
