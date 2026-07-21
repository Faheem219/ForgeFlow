# eval/results

Metrics reports written by the headless runner ([phase-44](../../plans/phase-44-headless-pipeline-runner-metrics.md)):

```bash
cd backend
uv run python -m app.eval.runner --spec all            # CI-safe legs
uv run python -m app.eval.runner --spec todo-list --with-deploy   # billable
```

Each run writes `eval-<timestamp>.json` containing every spec's record (first-pass vs post-repair
pass rate, repair iterations, regressions, tokens/₹, wall clock) plus corpus aggregates. The
dashboard ([phase-45](../../plans/phase-45-results-dashboard-report.md)) reads these files.

The `.json` files are generated output and are **gitignored** — they are measurements of a
particular run on particular hardware with a particular model, not source.
