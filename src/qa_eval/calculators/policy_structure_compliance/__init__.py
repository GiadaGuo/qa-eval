"""Policy structure compliance Calculator package.

Only :class:`PolicyStructureComplianceCalculator` is a platform Calculator;
the remaining modules are private implementation details included in its code
digest.
"""

from qa_eval.calculators.policy_structure_compliance.calculator import (
    PolicyStructureComplianceCalculator,
)

__all__ = ["PolicyStructureComplianceCalculator"]
