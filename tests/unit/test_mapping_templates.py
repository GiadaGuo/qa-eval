from __future__ import annotations

from qa_eval.application.mapping_templates import MappingTemplateResolver

POLICY_FIELDS = [
    "iid",
    "zccjfl",
    "title",
    "content",
    "policyurl",
    "pubtime",
    "effectivetime",
    "href",
    "organization",
    "filenumber",
    "isPublic",
    "label",
    "source",
    "policyType",
    "registnumber",
    "xobject",
    "xtheme",
    "releaseunit",
    "validity",
    "linkhref",
    "zcjdbt",
    "tjlj",
    "tjbt",
    "spjdlj",
    "spjdbt",
    "bmjdlj",
    "bmjdt",
    "qylj",
    "qtjd",
    "catalogid",
    "level",
    "status",
    "attachname",
    "attachurl",
    "issue",
    "publishunit",
    "type",
    "targetContent",
    "formattime",
]


def test_policy_schema_resolves_versioned_template() -> None:
    resolved = MappingTemplateResolver().resolve(POLICY_FIELDS)

    assert resolved is not None
    assert resolved.template_id == "policy.policy"
    assert resolved.template_version == "1.0.0"
    assert resolved.score == 1.0
    assert resolved.mapping.adapter.key == "jhsjk_policy_article"
    content_rules = [
        rule for rule in resolved.mapping.mappings if rule.source_path == "$.content"
    ]
    assert [rule.target.role for rule in content_rules if rule.target] == [
        "article.body.html",
        "article.body.plain",
    ]


def test_unrelated_schema_does_not_match_policy_template() -> None:
    resolver = MappingTemplateResolver()
    fields = ["id", "text", "created_at"]
    assert resolver.resolve(fields) is None
    explanation = resolver.explain(fields)
    assert explanation["matched"] is False
    assert "missing required fields" in explanation["reason"]


def test_partial_lookalike_does_not_match_policy_template() -> None:
    assert MappingTemplateResolver().resolve(["iid", "title", "content"]) is None
