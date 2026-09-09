"""Editing harness package.

The initializer stays side-effect free so Temporal can import workflow definitions
inside its sandbox. Persistence code is imported explicitly at activity boundaries.
"""
