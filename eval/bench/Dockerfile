# Execution image for the industry code benchmarks (HumanEval, MBPP, HumanEval-TS).
#
# Model-written code runs here and nowhere else (golden rule 3). The runner starts it with no
# network, every capability dropped, a read-only root, and CPU/memory/pid limits — see
# backend/app/eval/bench/executor.py. Network is needed only to *build* the image.
#
#   docker build -t forgeflow-bench:latest eval/bench     (or: make bench-image)
FROM node:20-bookworm-slim

# python3 = 3.11 on bookworm (HumanEval/MBPP need only the stdlib).
# typescript is pinned: MultiPL-E grades TS by compiling with `tsc --target esnext`, then running node.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g typescript@5.6.3 \
    && npm cache clean --force

# The base image's unprivileged `node` user (uid 1000).
USER node
WORKDIR /work
