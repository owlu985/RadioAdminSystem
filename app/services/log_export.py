from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import csv
import io
import os
import zipfile
from typing import Iterable


@dataclass
class LogCsvEntry:
    show_date: str
    show_name: str
    dj: str
    time: str
    entry_type: str
    title: str
    artist: str
    message: str


def recording_csv_path(recording_path: str) -> str:
    base, _ = os.path.splitext(recording_path)
    return f"{base}.csv"


def build_recording_base_path(
    *,
    output_root: str,
    show_name: str,
    show_date: date,
    auto_create_show_folders: bool,
) -> str:
    safe_display = show_name.replace("/", "_").replace("\\", "_")
    safe_name = safe_display.replace(" ", "_")
    suffix = show_date.strftime("%m-%d-%y")
    folder = output_root
    if auto_create_show_folders:
        folder = os.path.join(output_root, safe_display)
    filename = f"{safe_name}_{suffix}_RAWDATA"
    return os.path.join(folder, filename)


def write_log_csv(
    *,
    csv_path: str,
    entries: Iterable[LogCsvEntry],
) -> str:
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Show Date", "Show Name", "DJ", "Time", "Type", "Title", "Artist", "Message"])
        for entry in entries:
            writer.writerow([
                entry.show_date,
                entry.show_name,
                entry.dj,
                entry.time,
                entry.entry_type,
                entry.title,
                entry.artist,
                entry.message,
            ])
    return csv_path


def read_log_csv(csv_path: str) -> list[LogCsvEntry]:
    entries: list[LogCsvEntry] = []
    if not os.path.exists(csv_path):
        return entries
    with open(csv_path, "r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            entries.append(
                LogCsvEntry(
                    show_date=row.get("Show Date", ""),
                    show_name=row.get("Show Name", ""),
                    dj=row.get("DJ", ""),
                    time=row.get("Time", ""),
                    entry_type=row.get("Type", ""),
                    title=row.get("Title", ""),
                    artist=row.get("Artist", ""),
                    message=row.get("Message", ""),
                )
            )
    return entries


def build_docx(entries: Iterable[LogCsvEntry]) -> bytes:
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"
 xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"
 xmlns:v="urn:schemas-microsoft-com:vml"
 xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:w10="urn:schemas-microsoft-com:office:word"
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
 xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
 xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk"
 xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml"
 xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" mc:Ignorable="w14 wp14">
  <w:body>
    {rows}
    <w:sectPr/>
  </w:body>
</w:document>
"""
    row_template = """
    <w:p><w:r><w:t>{show_date} - {show_name} ({dj})</w:t></w:r></w:p>
    <w:p><w:r><w:t>{time} | {entry_type} | {title} | {artist}</w:t></w:r></w:p>
    """
    rows_xml = ""
    for entry in entries:
        rows_xml += row_template.format(
            show_date=entry.show_date,
            show_name=entry.show_name,
            dj=entry.dj,
            time=entry.time,
            entry_type=entry.entry_type,
            title=entry.title,
            artist=entry.artist,
        )

    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml.format(rows=rows_xml))
    mem.seek(0)
    return mem.getvalue()
