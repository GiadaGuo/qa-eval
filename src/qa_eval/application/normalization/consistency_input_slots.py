"""Bind each configured 0207/0208 field to an explicit v3 selector."""

from qa_eval.calculators.resolved_consistency_fields import field_specs
from qa_eval.contracts.logical_inputs import InputSlot

DYNAMIC_FIELDS = {
    ("field_relation_consistency", "2.1.0"),
    ("field_relation_consistency", "2.2.0"),
    ("cross_record_duplicate", "1.1.0"),
    ("intra_record_repetition", "1.1.0"),
}


def consistency_input_contract(calculator, parameters):
    contract = calculator.input_contract.logical
    if (calculator.calculator_key, calculator.version) not in DYNAMIC_FIELDS:
        return contract
    if "fields" not in parameters:
        raise ValueError(
            "FIELD_NOT_SELECTED: configure fields and their input selectors"
        )
    specs = field_specs(calculator.calculator_key, parameters)
    return contract.model_copy(
        update={
            "slots": tuple(
                InputSlot(
                    name=name,
                    semantic="record.field",
                    representation="json",
                    value_type="any",
                    selectable=True,
                )
                for name in sorted(specs)
            )
        }
    )


def resolve_consistency_representations(calculator, contract, selectors, mapping):
    if (
        calculator.calculator_key,
        calculator.version,
    ) not in DYNAMIC_FIELDS or mapping is None:
        return contract
    representations = {}
    for binding in mapping.bindings:
        representations.setdefault(binding.semantic, set()).add(binding.representation)
    slots = []
    for slot in contract.slots:
        selector = selectors.get(slot.name)
        choices = representations.get(selector.semantic, set()) if selector else set()
        if len(choices) > 1:
            raise ValueError(
                f"INPUT_INCOMPATIBLE: ambiguous representation for {slot.name}"
            )
        slots.append(
            slot.model_copy(update={"representation": next(iter(choices))})
            if choices
            else slot
        )
    return contract.model_copy(update={"slots": tuple(slots)})


def validate_field_relation_bindings(parameters, plan, raw_fields):
    """Validate resolved source identities and types for a newly configured graph."""
    bindings = {entry.slot.name: entry.binding for entry in plan.slots}
    for alias, spec in parameters["fields"].items():
        binding = bindings.get(alias)
        if binding is None:
            raise ValueError(f"FIELD_NOT_SELECTED: {alias}")
        expected = spec["value_type"]
        accepted = {
            "text": {"string", "text"},
            "string": {"string", "text"},
            "number": {"integer", "float", "number"},
            "datetime": {"string", "datetime"},
        }.get(expected, {expected})
        tokens = binding.field_tokens
        if binding.transform:
            actual = {"string"}
        elif len(tokens) == 1:
            actual = raw_fields.get(tokens[0], set())
        else:
            actual = set()
        if not actual and binding.definition and binding.definition.type != "any":
            actual = {binding.definition.type}
        if actual and not actual <= accepted:
            raise ValueError(f"INPUT_INCOMPATIBLE: {alias} expects {expected}")

    edges = set()
    for relation in parameters["relations"]:
        left, right = (bindings[relation[side]].field_tokens for side in ("left", "right"))
        if left == right:
            raise ValueError(f"INPUT_INCOMPATIBLE: same-source relation {relation['id']}")
        edge = frozenset((left, right))
        if edge in edges:
            raise ValueError(f"INPUT_INCOMPATIBLE: duplicate source edge {relation['id']}")
        edges.add(edge)
