"""Massive (formerly Polygon.io) data access: S3 flat files and the reference REST API.

Standard library only, so ``scripts/massive.py`` runs with any Python 3.9+ on the owner's Mac
without installing anything (the research VM cannot reach Massive). Secrets come from the
repository's ``.env`` and are never printed, logged or written anywhere else.
"""
