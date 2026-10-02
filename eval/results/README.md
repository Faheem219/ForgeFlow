# eval/results

Written by `make eval` (`python -m app.eval`):

| File | What |
|---|---|
| `REPORT.md` | The showcase report: the latest industry-benchmark run and the latest app-corpus run |
| `bench/bench-<timestamp>.json` | HumanEval, MBPP, HumanEval-TS, BFCL — every item's verdict, tokens, ₹, settings |
| `eval-<timestamp>.json` | The spec corpus through the real pipeline — per spec: criteria met and tests passing (first pass / post repair), iterations, tokens, ₹, time |

The `.json` files are gitignored: they measure one run, on one machine, with one model.
`python -m app.eval --report-only` rebuilds `REPORT.md` from the newest files.
The `/eval` dashboard reads the `eval-*.json` files.
