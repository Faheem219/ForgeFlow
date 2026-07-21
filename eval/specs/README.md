# eval/specs

Benchmark app specs for the evaluation harness (first-pass vs post-repair pass rate,
repair-iteration counts, cost, screenshot→URL time — D13).

**The format is documented in [`schema.md`](./schema.md).** Nine graded seeds live here, from a todo
list up to an event scheduler with overlap detection; `assets/` holds any screenshots they reference.

Validate and list them from `backend/`:

```bash
uv run python -m app.eval.specs_loader --list
```

Seeded in [phase-43](../../plans/phase-43-benchmark-spec-format-seeds.md); run headless in
[phase-44](../../plans/phase-44-headless-pipeline-runner-metrics.md).
