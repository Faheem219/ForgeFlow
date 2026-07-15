"""Pluggable design stage (D9): Stitch by default, Figma as an alternative, fake for offline.

This package owns the **provider-agnostic contract** only. Stage handlers and the UI (phase-19)
talk to :class:`~app.design.base.DesignProvider` and never to a concrete provider, so switching
providers is a config change (D9 acceptance) and a quota-exhausted Stitch can fail over to Figma
per project (D10).
"""
