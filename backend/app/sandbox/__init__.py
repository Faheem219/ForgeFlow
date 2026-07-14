"""Per-project sandbox lifecycle (phase-11).

Owns the ``Project ↔ container`` mapping and enforces the runtime isolation posture
(no host network, dropped caps, cpu/mem/pid limits) at ``docker run`` time. This package
is lifecycle + isolation + reaping only; the filesystem, exec/terminal, and preview
surfaces are later phases (12/13/15) that build on the container this manager provides.
"""
