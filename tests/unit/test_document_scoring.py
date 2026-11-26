import json
from fractions import Fraction
from pathlib import Path

import pytest

from qa_eval.application.evaluation.scoring import (
    ComponentScore,
    score_document_aspects,
    weighted_required_scores,
)
from qa_eval.application.evaluation.unified_report import standard_verdict

EXPECTED = json.loads(
    (
        Path(__file__).parents[1] / "fixtures/document-completeness/expected.json"
    ).read_text()
)["expectations"]


def component(key, a, b, weight=Fraction(1, 4), status="OK"):
    score = score_document_aspects(
        status, {"satisfied_aspect_count": a, "required_aspect_count": b}
    )
    return ComponentScore(key, "CALCULATOR_RESULT", None, score, weight, weight)


@pytest.mark.parametrize(
    ("fixture", "points"), [("complete", 100), ("partial", 25), ("adversarial", 0)]
)
def test_policy_document_scores(fixture, points):
    parts = tuple(
        component(key, row["A"], row["B"]) for key, row in EXPECTED[fixture].items()
    )
    assert weighted_required_scores(parts).score.value == points


@pytest.mark.parametrize(
    ("weight", "verdict"), [(Fraction(1, 2), "PASS"), (Fraction(51, 100), "FAIL")]
)
def test_exact_threshold_with_custom_weights(weight, verdict):
    result = weighted_required_scores(
        (
            component("0101", 4, 5, weight),
            *(
                component(key, 5, 5, (1 - weight) / 3)
                for key in ("0102", "0103", "0104")
            ),
        )
    )
    assert result.score.value == 100 - 20 * weight
    assert standard_verdict(result.score) == verdict


@pytest.mark.parametrize("status", ["ERROR", "NOT_APPLICABLE", "MISSING", "OK"])
def test_required_unscoreable_aspect_keeps_weights(status):
    parts = (
        component("0101", 0, 0, status=status),
        *(component(key, 5, 5) for key in ("0102", "0103", "0104")),
    )
    result = weighted_required_scores(parts)
    assert result.score.value is None
    assert standard_verdict(result.score) == "INDETERMINATE"
    assert all(part.effective_weight == Fraction(1, 4) for part in result.components)
