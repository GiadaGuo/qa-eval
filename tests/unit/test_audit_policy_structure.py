import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_policy_structure.py"
SPEC = importlib.util.spec_from_file_location("audit_policy_structure", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _write(path: Path) -> None:
    rows = [
        {
            "iid": "n1",
            "title": "测试通知",
            "policyType": "通知",
            "content": "<p>第一条 总则</p><p>第二条 内容</p>",
        },
        {"iid": "m1", "title": "测试办法", "content": "一、目标\n（一）任务\n1. 行动"},
        {"iid": "r1", "title": "测试条例", "content": "普通正文"},
        {
            "iid": "p1",
            "title": "发展规划纲要",
            "content": "<div><img src='x.png'></div>",
            "attachurl": "x.pdf",
        },
        {"iid": "o1", "title": "新闻", "content": "<p>未闭合"},
    ]
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_audit_is_deterministic_and_redacts_content(tmp_path: Path) -> None:
    source = tmp_path / "policy.jsonl"
    _write(source)
    first = MODULE.audit(source, sample_size=2, seed=7)
    second = MODULE.audit(source, sample_size=2, seed=7)
    assert first == second
    assert first["input"]["record_count"] == 5
    assert first["full_scan_counts"]["document_type"] == {
        "notification": 1,
        "measure": 1,
        "regulation": 1,
        "plan": 1,
        "other": 1,
    }
    assert first["full_scan_counts"]["strata"]["notification_with_legal_clauses"] == 1
    assert first["full_scan_counts"]["strata"]["attachment_only"] == 1
    assert "第一条" not in json.dumps(first, ensure_ascii=False)


def test_sample_profile_distinguishes_candidate_modes(tmp_path: Path) -> None:
    source = tmp_path / "policy.jsonl"
    _write(source)
    result = MODULE.audit(source, sample_size=5, seed=9)
    modes = result["sample_profile"]["structure_mode_candidate_counts"]
    assert modes["LEGAL_HIERARCHY"] == 1
    assert modes["OUTLINE_HIERARCHY"] == 1
    assert modes["GENERIC_DOCUMENT"] == 3
    assert result["sample_profile"]["html"]["tag_counts"]["p"] == 3
