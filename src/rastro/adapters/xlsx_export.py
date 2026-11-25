"""Minimal XLSX writer on the standard library: one sheet, every cell an inline string."""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

from rastro.application.tabular import records_table
from rastro.domain.dataset import StoredRecord

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
# Everything outside the XML 1.0 Char production: C0 controls, surrogates, U+FFFE, U+FFFF.
_XML_FORBIDDEN = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def column_name(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _cell(ref: str, text: str) -> str:
    clean = escape(_XML_FORBIDDEN.sub("", text))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{clean}</t></is></c>'


def _sheet(header: list[str], rows: list[list[str]]) -> str:
    lines = []
    for number, row in enumerate([header, *rows], start=1):
        cells = "".join(_cell(f"{column_name(i)}{number}", text) for i, text in enumerate(row))
        lines.append(f'<row r="{number}">{cells}</row>')
    return f'{_DECL}<worksheet xmlns="{_MAIN}"><sheetData>{"".join(lines)}</sheetData></worksheet>'


def xlsx_export(records: tuple[StoredRecord, ...]) -> bytes:
    """Workbook bytes with the same columns and formula guard as the CSV export."""
    header, rows = records_table(records)
    parts = {
        "[Content_Types].xml": (
            f'{_DECL}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
        ),
        "_rels/.rels": (
            f'{_DECL}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships"><Relationship Id="rId1" Type="{_REL}/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            f'{_DECL}<workbook xmlns="{_MAIN}" xmlns:r="{_REL}"><sheets>'
            '<sheet name="Dataset" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            f'{_DECL}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships"><Relationship Id="rId1" Type="{_REL}/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": _sheet(header, rows),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()
