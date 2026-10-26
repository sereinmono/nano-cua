"""Concrete adapters for the minimal CUA base.

Submodules are imported explicitly by the composition root (``cli.py``) so that
importing the core package never pulls in playwright or an HTTP client.
"""
