import pytest

from qa_eval.calculators.policy_document_format.matching import inspect
from qa_eval.calculators.policy_document_format.structure import blocks


@pytest.mark.parametrize(
    ("kind", "body"),
    [
        ("通知", "请各单位认真组织开展专项工作，按期报送材料。"),
        ("意见", "总体要求：加强公共服务，完善日常管理。"),
        ("批复", "同意开展项目，具体事项按规定办理。"),
        ("函", "商请贵单位提供工作支持。"),
        ("办法", "各单位负责监督管理工作。"),
        ("规定", "各单位应当完善工作制度。"),
        ("细则", "申请人应当提交申请材料。"),
        ("方案", "主要目标：改善服务。<br>重点任务：推进试点工作。"),
    ],
)
def test_supported_paths(kind, body):
    result = inspect({"title": "关于专项工作的" + kind, "content": f"<p>{body}</p>"})
    conventional = result["segments"][0]
    assert not conventional["reasons"]
    assert any(path["status"] == "PASS" for path in conventional["paths"])
    assert all(rule["status"] != "FAIL" for rule in conventional["normative"])


def test_source_blocks_and_integrity():
    raw = "<h2>甲&amp;乙</h2><ul><li>第一项</li></ul><table><tr><td>单元</td></tr></table>"
    a, b, c = blocks(raw, "body-html")
    assert (a.kind, a.heading_level, b.kind, c.kind) == (
        "HEADING",
        2,
        "LIST_ITEM",
        "TABLE_CELL",
    )
    assert raw[a.start : a.end] == "甲&amp;乙"
    assert a.span()["content_id"] == "body-html"
    result = inspect(
        {"title": "关于工作的通知", "content": "<p>推进本单位工作。</p><sp"}
    )
    assert result["reasons"] == ["BODY_INTEGRITY_UNVERIFIED"]
    assert not result["segments"][0]["anchors"]


def test_conflict_missing_and_quoted_reference():
    title = "关于开展工作的通知"
    assert (
        "TYPE_CONFLICT"
        in inspect({"title": title, "content": "请组织开展工作。", "type": "解读"})[
            "segments"
        ][0]["reasons"]
    )
    assert "MISSING_BODY" in inspect({"title": title})["segments"][0]["reasons"]
    result = inspect(
        {
            "title": "关于执行《原管理办法》的通知",
            "content": "<p>依据旧制度，组织推进有关事项。</p><p>原管理办法</p><p>这是引用材料，用于说明背景。</p>",
        }
    )
    assert len(result["segments"]) == 1


def test_compound_and_unresolved():
    row = {
        "title": "关于印发《工作方案》的通知",
        "content": "<p>现将《工作方案》印发给你们，请贯彻执行。</p>",
    }
    assert "REQUIRED_ATTACHMENT_UNRESOLVED" in inspect(row)["reasons"]
    row["content"] += (
        "<h2>工作方案</h2><p>主要目标：促进项目落地。</p><p>重点任务：组织开展各项工作。</p>"
    )
    result = inspect(row)
    main, child = result["segments"]
    assert child["required"] and child["parent"] == main["id"]
    assert child["type_candidate"] == "方案"


def test_score_bounds_and_explicit_normative_negative():
    from qa_eval.calculators.policy_document_format.calculator import settings
    from qa_eval.calculators.policy_document_format.scoring import score_document

    base = {
        "title": "关于组织实施工作的通知",
        "filenumber": "某政发[2024]9号",
        "content": "<p>某政发[2024]9号</p><p>为促进工作落实，制定本通知。</p>"
        "<h2>一、组织实施</h2><p>请各单位认真组织开展相关工作。</p><h2>二、反馈</h2>",
    }
    result = inspect(base)
    assert (
        next(r for r in result["segments"][0]["normative"] if r["id"] == "N2")["status"]
        == "UNKNOWN"
    )
    result = inspect(
        base | {"verified_original": True, "verified_standard_scope": True}
    )
    assert (
        next(r for r in result["segments"][0]["normative"] if r["id"] == "N2")["status"]
        == "FAIL"
    )
    scored = score_document(result, settings({"by_type": {"通知": {"threshold": 100}}}))
    assert scored["label"] == 0
    assert scored["score"] < 100
    unavailable = score_document(inspect({"title": "通知"}), settings({}))
    assert (unavailable["label"], unavailable["score"]) == ("NOT_EVALUATED", None)


def test_compound_score_uses_required_segments():
    from qa_eval.calculators.policy_document_format.calculator import settings
    from qa_eval.calculators.policy_document_format.scoring import score_document

    content = "<p>现将《工作方案》印发给你们，请贯彻执行。</p>"
    document = inspect({"title": "关于印发《工作方案》的通知", "content": content})
    result = score_document(document, settings({"threshold": 0, "minimum_coverage": 0}))
    assert result["label"] == "NOT_EVALUATED"
    assert "REQUIRED_ATTACHMENT_UNRESOLVED" in result["reasons"]
