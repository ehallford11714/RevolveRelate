"""Complex analytics as RelOp scaffolds. Roll out on the dummy sandbox, then promote."""

from revolverelate.analytics.catalog import list_recipes, scaffold_ir
from revolverelate.analytics.plans import AnalyticsLibrary
from revolverelate.analytics.composites import check_chain, load_composite_rules
from revolverelate.analytics.primitives import (
    apply_primitive,
    chain,
    list_families,
    list_primitives,
    primitive_ids,
)

__all__ = [
    "AnalyticsLibrary",
    "apply_primitive",
    "chain",
    "check_chain",
    "list_families",
    "list_primitives",
    "list_recipes",
    "load_composite_rules",
    "primitive_ids",
    "scaffold_ir",
]
