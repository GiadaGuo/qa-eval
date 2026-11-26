"""Background XLSX exports of the immutable Decision index."""

import logging
import math
import re
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from itertools import islice
from pathlib import Path
from threading import Lock
from uuid import UUID
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZIP_DEFLATED, ZipFile

from qa_eval.application.evidence.index import (
    ArtifactIndexSpec,
    ArtifactRecordIndexService,
)

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_SHEET_CAPACITY = 1_048_575
logger = logging.getLogger(__name__)


def _row(values):
    cells = (
        f"<c><v>{value}</v></c>"
        if isinstance(value, (int, float))
        else '<c t="inlineStr"><is><t xml:space="preserve">'
        + escape(_INVALID_XML.sub(lambda match: f"\\u{ord(match[0]):04x}", value or ""))
        + "</t></is></c>"
        for value in values
    )
    return ("<row>" + "".join(cells) + "</row>").encode()


def _cell_parts(value):
    if type(value) is int or (type(value) is float and math.isfinite(value)):
        return [value]
    text = str(value) if value is not None else ""
    if len(text) <= 5_000:
        return [text]
    # Count UTF-16 code units (including characters outside the BMP). The
    # chunk size also leaves room for continuation labels and escaped controls.
    text = _INVALID_XML.sub(lambda match: f"\\u{ord(match[0]):04x}", text)
    encoded = text.encode("utf-16-le")
    result = []
    start = 0
    while start < len(encoded):
        end = min(start + 60_000, len(encoded))
        if (
            end < len(encoded)
            and 0xD800 <= int.from_bytes(encoded[end - 2 : end], "little") <= 0xDBFF
        ):
            end -= 2
        result.append(encoded[start:end].decode("utf-16-le"))
        start = end
    return result


def _sheet_rows(records):
    for record in records:
        columns = [_cell_parts(value) for value in record]
        count = max(map(len, columns), default=1)
        for index in range(count):
            yield [
                parts[0]
                if index == 0
                else f"[续 {index + 1}/{len(parts)}] {parts[index]}"
                if index < len(parts)
                else ""
                for parts in columns
            ]


def write_workbook(path: Path, records):
    sheets = [
        (
            "未判定记录",
            ("样本 ID", "最终状态", "涉及原因代码"),
            ((sample_id, "未判定", reasons) for sample_id, reasons in records),
        )
    ]
    write_sheets(path, sheets)


def write_sheets(path: Path, tables):
    with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=3) as workbook:
        sheets = []
        for name, header, records in tables:
            source = _sheet_rows(records)
            part = 1
            first = next(source, None)
            while part == 1 or first is not None:
                number = len(sheets) + 1
                sheet = f"worksheets/sheet{number}.xml"
                sheets.append((name if part == 1 else f"{name} {part}", sheet))
                with workbook.open(f"xl/{sheet}", "w", force_zip64=True) as output:
                    output.write(f'<worksheet xmlns="{_MAIN}"><sheetData>'.encode())
                    output.write(_row(header))
                    if first is not None:
                        output.write(_row(first))
                        for row in islice(source, _SHEET_CAPACITY - 1):
                            output.write(_row(row))
                    output.write(b"</sheetData></worksheet>")
                first = next(source, None)
                part += 1
        workbook.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{_PACKAGE}"><Relationship Id="rId1" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        workbook.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{_MAIN}" xmlns:r="{_REL}"><sheets>'
            + "".join(
                f'<sheet name={quoteattr(name)} sheetId="{i}" r:id="rId{i}"/>'
                for i, (name, _) in enumerate(sheets, 1)
            )
            + "</sheets></workbook>",
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<Relationships xmlns="{_PACKAGE}">'
            + "".join(
                f'<Relationship Id="rId{i}" Type="{_REL}/worksheet" Target="{sheet}"/>'
                for i, (_, sheet) in enumerate(sheets, 1)
            )
            + "</Relationships>",
        )
        workbook.writestr(
            "[Content_Types].xml",
            f'<Types xmlns="{_TYPES}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/{sheet}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for _, sheet in sheets
            )
            + "</Types>",
        )


class DecisionExportJobs:
    def __init__(self):
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="decision-export"
        )
        self._lock = Lock()
        self._jobs: dict[tuple[UUID, str | None], Future[Path]] = {}
        self._directory = None

    def start(
        self,
        evidence_id,
        reason,
        indexes: ArtifactRecordIndexService,
        spec: ArtifactIndexSpec,
    ):
        def generate(path):
            with closing(indexes.iter_decision_export(spec, reason)) as records:
                write_workbook(path, records)

        self.start_workbook(evidence_id, reason, generate)

    def start_workbook(self, evidence_id, reason, generate):
        key = (evidence_id, reason)
        with self._lock:
            previous = self._jobs.get(key)
            if previous and (not previous.done() or previous.exception() is None):
                return
            if len(self._jobs) >= 16 and previous is None:
                expired = next(
                    (key for key, future in self._jobs.items() if future.done()), None
                )
                if expired is None:
                    raise ValueError("导出队列已满，请稍后重试")
                old = self._jobs.pop(expired)
                if old.exception() is None:
                    old.result().unlink(missing_ok=True)
            if self._directory is None:
                self._directory = tempfile.TemporaryDirectory(
                    prefix="qa-decision-export-"
                )
            self._jobs[key] = self._executor.submit(
                self._generate, self._directory.name, generate
            )

    @staticmethod
    def _generate(directory, generate):
        with tempfile.NamedTemporaryFile(
            dir=directory, suffix=".xlsx", delete=False
        ) as temporary:
            path = Path(temporary.name)
        try:
            generate(path)
        except Exception:
            path.unlink(missing_ok=True)
            logger.exception("Decision export failed")
            raise
        return path

    def status(self, evidence_id, reason):
        with self._lock:
            future = self._jobs.get((evidence_id, reason))
            if future is None:
                return "NOT_REQUESTED"
            if not future.done():
                return "RUNNING"
            return "FAILED" if future.exception() else "READY"

    def open(self, evidence_id, reason):
        with self._lock:
            future = self._jobs.get((evidence_id, reason))
            if future and future.done() and future.exception() is None:
                return future.result().open("rb")
        return None


decision_export_jobs = DecisionExportJobs()
