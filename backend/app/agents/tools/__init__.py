"""Agent tools bound to the project sandbox (phase-21).

The strictly-schemaed surface the codegen/test/repair agents act through — every effect goes to the
project's sandbox via the phase-12 FS/git, phase-13 exec, and phase-15 preview layers, **never** the
host (§7). Args are schema-validated; unsafe calls are rejected with structured errors fed back to
the model; each call is recorded on the ``Run`` trace.
"""
