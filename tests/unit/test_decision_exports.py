from xml.etree import ElementTree
from zipfile import ZipFile

from qa_eval.application.services import decision_exports


def test_workbook_streams_rows_across_sheets_as_literal_text(tmp_path, monkeypatch):
    monkeypatch.setattr(decision_exports, "_SHEET_CAPACITY", 2)
    path = tmp_path / "records.xlsx"
    decision_exports.write_workbook(
        path, iter([("s1", "=A1"), ("s2", "<&"), ("s3", None)])
    )
    with ZipFile(path) as workbook:
        assert workbook.testzip() is None
        first = ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        second = ElementTree.fromstring(workbook.read("xl/worksheets/sheet2.xml"))
        assert len(first.findall(".//{*}row")) == 3
        assert len(second.findall(".//{*}row")) == 2
        assert [cell.text for cell in first.findall(".//{*}t")][-4:] == [
            "=A1",
            "s2",
            "未判定",
            "<&",
        ]
        assert first.findall(".//{*}f") == []


def test_excel_continuations_preserve_long_unicode_cells(tmp_path):
    value = "甲🙂" * 21_000
    path = tmp_path / "long.xlsx"
    decision_exports.write_sheets(path, [("长证据", ("证据",), [(value,)])])
    with ZipFile(path) as workbook:
        xml = ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        parts = [cell.text for cell in xml.findall(".//{*}t")][1:]
        assert len(parts) > 1
        assert all(len(part.encode("utf-16-le")) // 2 <= 32767 for part in parts)
        assert parts[0] + "".join(part.split("] ", 1)[1] for part in parts[1:]) == value
