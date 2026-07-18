"""Test execution + result parsing (phase-28).

Runs the generated unit (Vitest/Jest) and Playwright suites in the sandbox and normalizes the three
reporters into ONE :class:`~app.testing.models.TestResult` shape — the measurement layer the repair
loop (Epic 6) consumes. Structured, per-test, criterion-tagged results with referenced source files
are what make minimal repair context (phase-29) possible.
"""
