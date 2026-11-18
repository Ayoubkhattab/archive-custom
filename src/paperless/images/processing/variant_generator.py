"""Responsive variants for srcset."""

from __future__ import annotations

VARIANT_WIDTHS: tuple[tuple[str, int], ...] = (
    ("thumbnail", 200),
    ("small", 640),
    ("medium", 1280),
    ("large", 2048),
)


def plan_variants(original_width: int) -> list[tuple[str, int]]:
    """
    Which (kind, width) to generate without upscaling: every kind narrower
    than the original, plus the next kind capped at the original width, so
    the largest variant is always full resolution (up to 2048px).
    """
    plan: list[tuple[str, int]] = []
    for kind, width in VARIANT_WIDTHS:
        if width < original_width:
            plan.append((kind, width))
        else:
            plan.append((kind, original_width))
            break
    return plan
