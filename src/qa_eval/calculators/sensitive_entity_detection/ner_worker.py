"""JSON-lines UIE worker executed by the isolated ner-eval interpreter."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _emit(value: dict) -> None:
    sys.stdout.write(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    sys.stdout.flush()


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    model_dir, vendor_src = map(Path, sys.argv[1:])
    sys.path.insert(0, str(vendor_src))
    try:
        from uie_pytorch import UIEExtractor

        extractor = UIEExtractor(
            model=str(model_dir),
            schema=["详细住址", "涉案人员", "内部批示", "身份证号", "手机号"],
            device="cpu",
            batch_size=1,
        )
    except Exception as exc:  # noqa: BLE001 - isolate third-party model errors
        _emit(
            {"status": "ERROR", "code": "MODEL_INIT_FAILED", "type": type(exc).__name__}
        )
        return 3
    _emit({"status": "READY"})
    label_map = {
        "详细住址": "DETAILED_ADDRESS",
        "涉案人员": "CASE_INVOLVED_PERSON",
        "内部批示": "INTERNAL_INSTRUCTION",
        "身份证号": "PRC_RESIDENT_ID",
        "手机号": "MOBILE_PHONE_CN",
    }
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            text = request["text"]
            response = extractor(text)[0]
            entities = []
            for original_label, values in response.items():
                for value in values:
                    start, end = value.get("start"), value.get("end")
                    if not isinstance(start, int) or not isinstance(end, int):
                        continue
                    if not 0 <= start < end <= len(text) or text[
                        start:end
                    ] != value.get("text"):
                        continue
                    entities.append(
                        {
                            "category": label_map.get(original_label, original_label),
                            "original_label": original_label,
                            "start": start,
                            "end": end,
                            "probability": float(value["probability"]),
                        }
                    )
            _emit(
                {
                    "status": "OK",
                    "request_id": request.get("request_id"),
                    "entities": entities,
                }
            )
        except Exception as exc:  # noqa: BLE001 - never echo request text
            _emit(
                {
                    "status": "ERROR",
                    "code": "INFERENCE_FAILED",
                    "type": type(exc).__name__,
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
