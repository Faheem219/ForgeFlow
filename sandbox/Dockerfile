# ForgeFlow sandbox base image — where untrusted, generated apps run (D5, §4, §7).
#
# The control plane NEVER execs generated code; it runs only inside a container built from this
# image. Runtime isolation (no host network, dropped caps, cpu/mem/pids limits) is applied by the
# sandbox MANAGER (phase-11) at `docker run` time, not baked here — so the image stays reusable.
#
# Contents: Node 20 + pnpm (corepack) + git + curl + Playwright chromium (shared, world-readable),
# a non-root `app` user (uid 1000), and a workspace at /workspace. No secrets are baked in.
FROM node:20-bookworm-slim

# Pinned so builds are reproducible; bump deliberately.
ARG PLAYWRIGHT_VERSION=1.48.2

ENV DEBIAN_FRONTEND=noninteractive \
    PNPM_HOME=/pnpm \
    PATH=/pnpm:/usr/local/bin:$PATH \
    WORKSPACE=/workspace \
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
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN corepack enable

# Playwright + chromium and its OS dependencies (installed as root via --with-deps). Kept in a fixed
# location and made world-readable so the non-root `app` user can launch the browser at runtime.
RUN mkdir -p /opt/pw \
    && cd /opt/pw \
    && npm init -y >/dev/null 2>&1 \
    && npm install --no-save "playwright@${PLAYWRIGHT_VERSION}" \
    && npx playwright install --with-deps chromium \
    && chmod -R a+rX /opt/pw /ms-playwright

# Non-root user at uid 1000. The base node image already owns uid 1000 (its `node` user); reclaim it
# for `app` so the workspace has a predictable, unprivileged owner.
RUN userdel -r node 2>/dev/null || true \
    && useradd --uid 1000 --user-group --create-home --shell /bin/bash app \
    && mkdir -p "${WORKSPACE}" \
    && chown app:app "${WORKSPACE}"

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR ${WORKSPACE}
USER app

# tini reaps zombies + forwards signals; entrypoint keeps the container alive for exec/preview and
# never auto-runs untrusted code.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD []
