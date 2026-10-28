from pathlib import Path
from uuid import uuid4

import pytest

from qa_eval.calculators.protocol import CalculatorContext, CalculatorValidationError
from qa_eval.calculators.text_duplicate_degree import TextDuplicateDegreeCalculator


def test_semantic_parameter_validation_rejects_incompatible_lsh_shape(
    tmp_path: Path,
) -> None:
    context = CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key="02080103",
        metric_version="1.0.0",
        input_path=tmp_path / "input.jsonl",
        output_dir=tmp_path / "output",
        input_schema_ref="2.0",
    )

    with pytest.raises(CalculatorValidationError, match="evenly divisible") as error:
        TextDuplicateDegreeCalculator().validate(
            context,
            {"minhash_permutations": 63, "lsh_bands": 16},
        )

    assert error.value.code == "CALCULATOR_VALIDATION_ERROR"
