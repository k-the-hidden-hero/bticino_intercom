"""Unit tests for the BTicino utility helpers."""

import pytest

from custom_components.bticino_intercom.const import (
    SUBTYPE_DOORLOCK,
    SUBTYPE_EXTERNAL_UNIT,
    SUBTYPE_STAIRCASE_LIGHT,
)
from custom_components.bticino_intercom.utils import resolve_module_subtype


@pytest.mark.parametrize(
    ("module_data", "expected"),
    [
        # EOS (bridge BNCX): only the raw 'type' is present, no 'variant'.
        ({"type": "BNDL"}, SUBTYPE_DOORLOCK),
        ({"type": "BNSL"}, SUBTYPE_STAIRCASE_LIGHT),
        ({"type": "BNEU"}, SUBTYPE_EXTERNAL_UNIT),
        # Legacy 100X/300X: a redundant 'variant' yields the same subtype.
        ({"type": "BNDL", "variant": "BNDL:bndl_doorlock"}, SUBTYPE_DOORLOCK),
        ({"type": "BNSL", "variant": "BNSL:bnsl_staircase_light"}, SUBTYPE_STAIRCASE_LIGHT),
        ({"type": "BNEU", "variant": "BNEU:bneu_external_unit"}, SUBTYPE_EXTERNAL_UNIT),
        # Forward-compat override: an UNKNOWN variant subtype wins over the type.
        ({"type": "BNEU", "variant": "BNEU:bneu_future_thing"}, "bneu_future_thing"),
        # A malformed variant (no colon) is ignored — fall back to the type.
        ({"type": "BNDL", "variant": "garbage"}, SUBTYPE_DOORLOCK),
        # Bridge / unmapped type without a usable variant → None (no entity).
        ({"type": "BNCX"}, None),
        ({"type": "BNC1"}, None),
        # Bridge with a legacy (unknown) variant → its own subtype, matched by
        # no platform, so still no entity is created.
        ({"type": "BNC1", "variant": "BNC1:bnc1_bridge"}, "bnc1_bridge"),
        # Empty / missing data → None.
        ({}, None),
    ],
)
def test_resolve_module_subtype(module_data: dict, expected: str | None) -> None:
    """The type is canonical; variant only overrides for unknown subtypes."""
    assert resolve_module_subtype(module_data) == expected
