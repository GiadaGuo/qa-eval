"""Score the observed template functions and preserve the uncertainty interval."""

from .matching import library


def segment_score(segment, threshold, minimum):
    matching = [path for path in segment["paths"] if path["status"] == "PASS"]
    signals = {rule["id"]: rule for rule in segment["signals"]}
    weights = library()["proposed_weights"]
    states = {key: "UNKNOWN" for key in weights if key != "normative"}
    path = matching[0] if matching else None
    states["body"] = "PASS" if path else "UNKNOWN"
    for zone, identifiers in {
        "title": {"subject_title"},
        "opening": {
            "purpose",
            "addressee",
            "reference_received",
            "first_notice",
            "parent_basis",
        },
        "closing": {"effective", "followup", "signoff", "letter_end"},
        "structure": {"hierarchy", "attachment_boundary"},
    }.items():
        if any(signals.get(key, {}).get("status") == "PASS" for key in identifiers):
            states[zone] = "PASS"
    if states["closing"] == "UNKNOWN" and path and segment["last_clause"]:
        states["closing"] = (
            "PASS"  # A bounded final clause can close a document without a formula.
        )
    anchors = segment["anchors"]
    zones = {signals[a["signal"]]["zone"] for a in anchors}
    if path and len(anchors) >= 3 and len(zones) >= 3:
        states["structure"] = "PASS"

    style = [(state, weights[key]) for key, state in states.items()]
    norm = [
        rule["status"]
        for rule in segment["normative"]
        if rule["id"] in {"N2", "N3", "N4"}
    ]
    norm = [status for status in norm if status != "NOT_APPLICABLE"]
    cells = style + [(status, weights["normative"] / len(norm)) for status in norm]
    passed = sum(weight for status, weight in cells if status == "PASS")
    failed = sum(weight for status, weight in cells if status == "FAIL")
    applicable = sum(weight for _, weight in cells)
    observed = passed + failed
    coverage = observed / applicable
    lower, upper = 100 * passed / applicable, 100 * (applicable - failed) / applicable
    reasons = list(segment["reasons"])
    if not path:
        reasons.append("TEMPLATE_PATH_UNCONFIRMED")
    if len(anchors) < 2 or "body" not in zones:
        reasons.append("INSUFFICIENT_INDEPENDENT_EVIDENCE")
    if coverage < minimum:
        reasons.append("BELOW_MIN_COVERAGE")
    if any(
        rule["id"] == "N6" and rule["status"] == "UNKNOWN"
        for rule in segment["normative"]
    ):
        reasons.append("MANDATORY_REVIEW_PENDING")
    # PASS/FAIL weights form the observed score. The interval prevents a
    # conclusion when missing evidence could move it across the threshold.
    outcome = "NOT_EVALUATED"
    if not reasons:
        if lower >= threshold:
            outcome = 1
        elif upper < threshold:
            outcome = 0
        else:
            reasons.append("THRESHOLD_STRADDLED")
    style_pass = sum(weight for status, weight in style if status == "PASS")
    style_observed = sum(
        weight for status, weight in style if status in {"PASS", "FAIL"}
    )
    return {
        "path": path["id"] if path else None,
        "label": outcome,
        "score": lower if outcome != "NOT_EVALUATED" else None,
        "style_score": 100 * style_pass / style_observed
        if style_observed and path and not segment["reasons"]
        else None,
        "score_bounds": [lower, upper] if observed else None,
        "observed_score": 100 * passed / observed if observed else None,
        "coverage": coverage,
        "reasons": reasons,
        "dimensions": states,
        "independent_evidence_count": len(anchors),
    }


def score_document(document, settings):
    segments = []
    for part in document["segments"]:
        options = settings | settings["by_type"].get(part["type_candidate"], {})
        segments.append(
            segment_score(part, options["threshold"], options["minimum_coverage"])
        )
    required = [
        score
        for part, score in zip(document["segments"], segments, strict=True)
        if part["required"]
    ]
    reasons = sorted(
        set(document["reasons"]) | {r for score in required for r in score["reasons"]}
    )
    unavailable = (
        not required or reasons or any(s["label"] == "NOT_EVALUATED" for s in required)
    )
    return {
        "segments": segments,
        "style_score": min(s["style_score"] for s in required)
        if required and all(s["style_score"] is not None for s in required)
        else None,
        "label": "NOT_EVALUATED" if unavailable else min(s["label"] for s in required),
        "score": None if unavailable else min(s["score"] for s in required),
        "reasons": reasons,
    }
