from datetime import date

import pytest

from qa_eval.calculators.policy_effectiveness import rules

OBSERVED = date(2026, 11, 19)
EXPIRED = "EXPIRED_EVIDENCED"
WITHIN = "NOT_EXPIRED_BY_KNOWN_TERM"
UNKNOWN = "UNDETERMINED"


@pytest.mark.parametrize(
    ("body", "day", "status", "reason"),
    [
        ("本办法有效期至2026年11月19日。", "2026-11-19", WITHIN, "WITHIN_KNOWN_TERM"),
        ("本办法有效期至2026年11月19日。", "2026-11-20", EXPIRED, "KNOWN_END_PASSED"),
        (
            "本办法自2026年11月19日起废止。",
            "2026-11-18",
            UNKNOWN,
            "NO_DECISIVE_SELF_TERM",
        ),
        (
            "本办法自2026年11月19日起废止。",
            "2026-11-19",
            EXPIRED,
            "DATED_SELF_CESSATION",
        ),
        (
            "本办法自2024年2月29日起施行，有效期一年。",
            "2025-02-27",
            WITHIN,
            "WITHIN_KNOWN_TERM",
        ),
        (
            "本办法自2024年2月29日起施行，有效期一年。",
            "2025-02-28",
            EXPIRED,
            "KNOWN_END_PASSED",
        ),
        (
            "本办法自发布之日起施行，有效期二年。",
            "2026-11-19",
            UNKNOWN,
            "RELATIVE_TERM_WITHOUT_RELIABLE_ANCHOR",
        ),
        ("本办法有效期至2026年02月30日。", "2026-11-19", UNKNOWN, "UNRELIABLE_DATE"),
        (
            "项目有效期至2025年11月19日。",
            "2026-11-19",
            UNKNOWN,
            "NO_DECISIVE_SELF_TERM",
        ),
        (
            "原《示例管理办法》（示〔2022〕1号）同时废止。",
            "2026-11-19",
            UNKNOWN,
            "NO_DECISIVE_SELF_TERM",
        ),
        (
            "本办法有效期至2025年11月19日。本办法原有效期延长至2027年11月19日。",
            "2026-11-19",
            WITHIN,
            "WITHIN_KNOWN_TERM",
        ),
        (
            "本办法有效期至2025年11月19日。本办法有效期延长至2027年11月19日。",
            "2026-11-19",
            UNKNOWN,
            "CONFLICTING_SIGNALS",
        ),
        (
            "本办法有效期至2025年11月19日。本办法继续有效。",
            "2026-11-19",
            UNKNOWN,
            "CONFLICTING_SIGNALS",
        ),
        (
            "若本办法自2020年1月1日起施行，则另行通知。本办法有效期二年。",
            "2026-11-19",
            UNKNOWN,
            "RELATIVE_TERM_WITHOUT_RELIABLE_ANCHOR",
        ),
    ],
)
def test_effectiveness_boundaries(body, day, status, reason):
    applicability, state, trigger, _, _, signals = rules.evaluate(
        "示例管理办法", body, None, date(2020, 1, 1), date.fromisoformat(day), OBSERVED
    )
    assert (applicability, state, trigger) == ("APPLICABLE", status, reason)
    assert all(0 <= item["start"] < item["end"] <= len(body) for item in signals)


def test_title_marks_and_document_type_are_independent():
    title = "【已废止】示例管理办法"
    body = "正文。"
    assert rules.evaluate(title, body, None, None, OBSERVED, OBSERVED)[1] == EXPIRED
    assert (
        rules.evaluate(title, body, None, None, date(2025, 1, 1), OBSERVED)[1]
        == UNKNOWN
    )
    assert (
        rules.evaluate(title, body, None, None, date(2026, 11, 20), OBSERVED)[1]
        == UNKNOWN
    )
    assert rules.evaluate(title, body, "解读", None, OBSERVED, OBSERVED)[:2] == (
        "APPLICABILITY_UNCERTAIN",
        None,
    )
    assert rules.evaluate(
        "示例管理办法政策解读", body, "解读", None, OBSERVED, OBSERVED
    )[:2] == ("NOT_APPLICABLE", None)
    quoted = rules.evaluate(
        "关于印发《示例办法（已废止）》的通知", body, None, None, OBSERVED, OBSERVED
    )
    assert quoted[1] == UNKNOWN and not any(
        signal["rule_id"] == "R1" for signal in quoted[-1]
    )
