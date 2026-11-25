"""Test-environment helper for the Profiler tests.

SDRW validates every package against its JSON Schema, which needs ``jsonschema`` with Draft 2020-12 and the ``referencing`` package. On a machine that has
them nothing here changes. On one that does not, only that single schema step is skipped - every other SDRW check (checksums, structure, atomic write and
re-read) still runs - and ``SCHEMA_BYPASSED`` is True so a run can say so. A bypassed run is not a full verification of the on-disk schema.
"""
from __future__ import annotations

try:
    from jsonschema import Draft202012Validator  # noqa: F401
    import referencing  # noqa: F401
    SCHEMA_BYPASSED = False
except ImportError:
    import src.utils.spatial_formats.verification.invariants as _inv
    _inv._schema_check = lambda profile, document: None
    SCHEMA_BYPASSED = True
