# -*- coding: utf-8 -*-
"""Document parser for extracting magnet, ed2k, thunder, and torrent links from TXT, CSV, HTML, and .torrent files."""

import csv
import hashlib
import html as html_lib
import io
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from bs4 import BeautifulSoup

SUPPORTED_DOC_EXTENSIONS = (".txt", ".csv", ".html", ".htm", ".xlsx", ".xlsm", ".xls", ".torrent")
SUPPORTED_DOC_MIMES = {
    "text/plain",
    "text/csv",
    "application/csv",
    "text/html",
    "application/xhtml+xml",
    "application/x-bittorrent",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    "application/vnd.ms-excel",
}

TEXT_DECODE_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "gb2312", "utf-16")

# Strict single-link patterns (case-insensitive)
MAGNET_STRICT_RE = re.compile(
    r"^magnet:\?xt=urn:btih:(?:[a-fA-F0-9]{40}|[a-zA-Z2-7]{32})(?:&.+)?$",
    re.IGNORECASE,
)
ED2K_STRICT_RE = re.compile(
    r"^ed2k://\|file\|.+\|[0-9]+\|[a-fA-F0-9]{32}\|",
    re.IGNORECASE,
)
THUNDER_STRICT_RE = re.compile(r"^thunder://[a-zA-Z0-9=+/]+$", re.IGNORECASE)
TORRENT_URL_STRICT_RE = re.compile(
    r"^https?://[^\s\"'<>]+?\.torrent(?:\?[^\s\"'<>]*)?$",
    re.IGNORECASE,
)

# Inline extraction patterns for free text / CSV cells / HTML content
LINK_SEARCH_PATTERNS = (
    ("ed2k", re.compile(r"ed2k://\|file\|[^\|]+\|[0-9]+\|[a-fA-F0-9]{32}\|/?", re.IGNORECASE)),
    ("magnet", re.compile(r"magnet:\?xt=urn:btih:(?:[a-fA-F0-9]{40}|[a-zA-Z2-7]{32})(?:[&\?][^\s\"'<>\]\)]*)?", re.IGNORECASE)),
    ("thunder", re.compile(r"thunder://[a-zA-Z0-9=+/]+", re.IGNORECASE)),
    ("torrent", re.compile(r"https?://[^\s\"'<>\]\)]+?\.torrent(?:\?[^\s\"'<>\]\)]*)?", re.IGNORECASE)),
)

HTML_LINK_ATTRS = ("href", "src", "data-href", "data-url", "data-magnet", "data-link", "data-torrent", "value")


def is_supported_link_document(document) -> bool:
    """Return True if a Telegram Document is .txt / .csv / .html / .htm / .xlsx / .xlsm / .xls / .torrent."""
    if not document:
        return False
    fname = (getattr(document, "file_name", None) or "").strip().lower()
    mime = (getattr(document, "mime_type", None) or "").strip().lower()
    if any(fname.endswith(ext) for ext in SUPPORTED_DOC_EXTENSIONS):
        return True
    if mime in SUPPORTED_DOC_MIMES:
        return True
    return False


def get_document_base_and_ext(file_name: str) -> Tuple[str, str]:
    """Split document filename into (base_name, lowercase_ext)."""
    raw = (file_name or "file").strip()
    base, ext = os.path.splitext(raw)
    ext_lower = ext.lower()
    if ext_lower in SUPPORTED_DOC_EXTENSIONS:
        return base or "file", ext_lower
    return raw, ext_lower


def decode_text_bytes(raw_bytes: bytes) -> Optional[str]:
    """Decode uploaded document bytes trying UTF-8 (with BOM), GB18030/GBK, and UTF-16."""
    if raw_bytes is None:
        return None
    data = bytes(raw_bytes)
    for encoding in TEXT_DECODE_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _clean_extracted_url(url: str) -> str:
    cleaned = html_lib.unescape(str(url or "")).strip()
    cleaned = cleaned.strip("\"'<>")
    cleaned = cleaned.rstrip(",;.")
    # Preserve trailing slash on ed2k or magnet; only strip unbalanced trailing punctuation
    return cleaned.strip()


def classify_link(link: str) -> Optional[str]:
    """Return canonical kind ('magnet'|'ed2k'|'thunder'|'torrent') if valid, else None."""
    s = _clean_extracted_url(link)
    if not s:
        return None
    if MAGNET_STRICT_RE.match(s):
        return "magnet"
    if ED2K_STRICT_RE.match(s):
        return "ed2k"
    if THUNDER_STRICT_RE.match(s):
        return "thunder"
    if TORRENT_URL_STRICT_RE.match(s):
        return "torrent"
    return None


def _dedup_key(kind: str, link: str) -> str:
    """Build stable deduplication key (case-insensitive on btih hash / scheme)."""
    if kind == "magnet":
        m = re.search(r"xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", link, re.IGNORECASE)
        if m:
            return f"magnet:{m.group(1).lower()}"
    return f"{kind}:{link.lower()}"


def extract_links_from_text_chunk(text: str) -> List[Tuple[str, str]]:
    """Extract all valid (kind, link) pairs from a single cell/line/text snippet."""
    if not text:
        return []
    unescaped = html_lib.unescape(str(text)).strip()
    if not unescaped:
        return []

    whole_kind = classify_link(unescaped)
    if whole_kind:
        return [(whole_kind, _clean_extracted_url(unescaped))]

    found: List[Tuple[str, str]] = []
    for kind, pattern in LINK_SEARCH_PATTERNS:
        for match in pattern.findall(unescaped):
            cleaned = _clean_extracted_url(match)
            if classify_link(cleaned) == kind:
                found.append((kind, cleaned))

    if not found:
        for frag in unescaped.split():
            cleaned = _clean_extracted_url(frag)
            kind = classify_link(cleaned)
            if kind:
                found.append((kind, cleaned))
    return found


def _deduplicate_pairs(pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    seen = set()
    unique: List[Tuple[str, str]] = []
    for kind, link in pairs:
        key = _dedup_key(kind, link)
        if key not in seen:
            seen.add(key)
            unique.append((kind, link))
    return unique


def extract_links_from_txt_content(content: str) -> Tuple[List[Tuple[str, str]], str]:
    """Extract links and first non-link line from TXT text."""
    lines = content.splitlines()
    raw_pairs: List[Tuple[str, str]] = []
    first_non_link_line = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        line_pairs = extract_links_from_text_chunk(stripped)
        if line_pairs:
            raw_pairs.extend(line_pairs)
        elif not first_non_link_line:
            first_non_link_line = stripped
    return _deduplicate_pairs(raw_pairs), first_non_link_line


def extract_links_from_csv_content(content: str) -> Tuple[List[Tuple[str, str]], str]:
    """Extract magnet/ed2k/thunder/torrent links from CSV rows & cells (handles quotes and multiline cells)."""
    raw_pairs: List[Tuple[str, str]] = []
    try:
        reader = csv.reader(io.StringIO(content))
        for row in reader:
            for cell in row:
                if not cell:
                    continue
                for subline in cell.splitlines():
                    raw_pairs.extend(extract_links_from_text_chunk(subline))
    except csv.Error:
        for line in content.splitlines():
            raw_pairs.extend(extract_links_from_text_chunk(line))
    return _deduplicate_pairs(raw_pairs), ""


def extract_links_from_html_content(content: str) -> Tuple[List[Tuple[str, str]], str]:
    """Extract magnet/ed2k/thunder/torrent links from HTML attributes, anchor text, and embedded scripts/text."""
    raw_pairs: List[Tuple[str, str]] = []
    soup = BeautifulSoup(content, "html.parser")

    # 1. Inspect link-bearing attributes first (preferring href/data-magnet so dn= params stay intact)
    for tag in soup.find_all(True):
        if not hasattr(tag, "attrs") or not isinstance(tag.attrs, dict):
            continue
        for attr_name in HTML_LINK_ATTRS:
            val = tag.attrs.get(attr_name)
            if not val:
                continue
            if isinstance(val, (list, tuple)):
                for item in val:
                    raw_pairs.extend(extract_links_from_text_chunk(str(item)))
            else:
                raw_pairs.extend(extract_links_from_text_chunk(str(val)))

    # 2. Inspect visible text and raw script/code blocks for plain-text magnet/torrent links
    for text_node in soup.stripped_strings:
        raw_pairs.extend(extract_links_from_text_chunk(text_node))

    # 3. Fallback scan on raw HTML (catches links in comments or non-standard attributes)
    for line in content.splitlines():
        if any(tok in line.lower() for tok in ("magnet:", "ed2k://", "thunder://", ".torrent")):
            raw_pairs.extend(extract_links_from_text_chunk(line))

    return _deduplicate_pairs(raw_pairs), ""


def _bdecode_next(data: bytes, idx: int):
    """Minimal pure-Python bencode decoder returning (value, next_idx, info_raw_slice_if_any)."""
    if idx >= len(data):
        raise ValueError("Unexpected EOF in bencode data")
    ch = data[idx:idx + 1]
    if ch == b"i":
        end = data.index(b"e", idx + 1)
        return int(data[idx + 1:end]), end + 1
    if ch == b"l":
        idx += 1
        items = []
        while data[idx:idx + 1] != b"e":
            val, idx = _bdecode_next(data, idx)
            items.append(val)
        return items, idx + 1
    if ch == b"d":
        idx += 1
        mapping = {}
        while data[idx:idx + 1] != b"e":
            key, idx = _bdecode_next(data, idx)
            val_start = idx
            val, idx = _bdecode_next(data, idx)
            if key == b"info":
                mapping[b"__info_raw__"] = data[val_start:idx]
            mapping[key] = val
        return mapping, idx + 1
    if ch.isdigit():
        colon = data.index(b":", idx)
        length = int(data[idx:colon])
        start = colon + 1
        end = start + length
        if end > len(data):
            raise ValueError("Truncated bencode byte string")
        return data[start:end], end
    raise ValueError(f"Invalid bencode token at {idx}: {ch!r}")


def extract_magnet_from_torrent_bytes(raw_bytes: bytes, fallback_name: str = "") -> List[Tuple[str, str]]:
    """Convert a .torrent bencoded file into a magnet:?xt=urn:btih:... link."""
    if not raw_bytes:
        return []
    try:
        parsed, _ = _bdecode_next(bytes(raw_bytes), 0)
    except Exception:
        return []
    if not isinstance(parsed, dict) or b"__info_raw__" not in parsed:
        return []
    info_raw = parsed[b"__info_raw__"]
    info_hash = hashlib.sha1(info_raw).hexdigest()
    info_dict = parsed.get(b"info") if isinstance(parsed.get(b"info"), dict) else {}
    raw_name = info_dict.get(b"name.utf-8") or info_dict.get(b"name")
    display_name = ""
    if isinstance(raw_name, bytes):
        display_name = raw_name.decode("utf-8", errors="ignore").strip()
    if not display_name and fallback_name:
        display_name = fallback_name.strip()
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    if display_name:
        magnet += f"&dn={quote(display_name)}"
    return [("magnet", magnet)]


def _local_xml_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _natural_sheet_sort_key(name: str):
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", name)]


def extract_links_from_xlsx_bytes(raw_bytes: bytes) -> Tuple[List[Tuple[str, str]], str]:
    """
    Zero-dependency parser for Excel OOXML (.xlsx / .xlsm):
    Reads shared strings, worksheet cell values, inline strings, HYPERLINK() formulas,
    and external worksheet hyperlink relationships (.rels).
    """
    raw_pairs: List[Tuple[str, str]] = []
    if not raw_bytes:
        return [], ""

    try:
        zf = zipfile.ZipFile(io.BytesIO(bytes(raw_bytes)))
    except zipfile.BadZipFile:
        # Handle HTML/CSV/XML files mistakenly saved with .xlsx extension
        text = decode_text_bytes(raw_bytes)
        if text:
            if "<" in text and any(k in text.lower() for k in ("<html", "<table", "<?xml", "<workbook")):
                return extract_links_from_html_content(text)
            return extract_links_from_csv_content(text)
        return [], ""

    with zf:
        names = zf.namelist()

        # 1. Shared strings table
        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in names:
            try:
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in root:
                    if _local_xml_tag(si.tag) != "si":
                        continue
                    parts = [elem.text for elem in si.iter() if _local_xml_tag(elem.tag) == "t" and elem.text]
                    shared_strings.append("".join(parts))
            except ET.ParseError:
                pass

        # 2. Worksheet external hyperlinks (_rels/sheet*.xml.rels) — catches display-text hyperlinks
        rels_files = sorted(
            [n for n in names if n.startswith("xl/worksheets/_rels/") and n.endswith(".rels")],
            key=_natural_sheet_sort_key,
        )
        for rel_name in rels_files:
            try:
                root = ET.fromstring(zf.read(rel_name))
                for rel in root:
                    target = rel.attrib.get("Target") or ""
                    if target:
                        raw_pairs.extend(extract_links_from_text_chunk(target))
            except ET.ParseError:
                pass

        # 3. Worksheet cells (shared strings, inlineStr, str values, and HYPERLINK formulas)
        sheet_files = sorted(
            [n for n in names if re.match(r"^xl/worksheets/sheet\d+\.xml$", n, re.IGNORECASE)],
            key=_natural_sheet_sort_key,
        )
        for sheet_name in sheet_files:
            try:
                root = ET.fromstring(zf.read(sheet_name))
            except ET.ParseError:
                continue
            for elem in root.iter():
                if _local_xml_tag(elem.tag) != "c":
                    continue
                cell_type = elem.attrib.get("t", "")
                val_text = ""
                formula_text = ""
                inline_parts: List[str] = []
                for child in elem:
                    ctag = _local_xml_tag(child.tag)
                    if ctag == "v" and child.text:
                        val_text = child.text
                    elif ctag == "f" and child.text:
                        formula_text = child.text
                    elif ctag == "is":
                        inline_parts.extend(
                            t_el.text for t_el in child.iter() if _local_xml_tag(t_el.tag) == "t" and t_el.text
                        )
                if formula_text:
                    raw_pairs.extend(extract_links_from_text_chunk(formula_text))
                if cell_type == "s" and val_text.isdigit():
                    idx = int(val_text)
                    if 0 <= idx < len(shared_strings):
                        for line in shared_strings[idx].splitlines():
                            raw_pairs.extend(extract_links_from_text_chunk(line))
                elif cell_type == "inlineStr" and inline_parts:
                    for line in "".join(inline_parts).splitlines():
                        raw_pairs.extend(extract_links_from_text_chunk(line))
                elif val_text:
                    for line in val_text.splitlines():
                        raw_pairs.extend(extract_links_from_text_chunk(line))

        # 4. Catch any shared strings not referenced by standard sheetN.xml names
        for s in shared_strings:
            for line in s.splitlines():
                raw_pairs.extend(extract_links_from_text_chunk(line))

    return _deduplicate_pairs(raw_pairs), ""


def extract_links_from_xls_bytes(raw_bytes: bytes) -> Tuple[List[Tuple[str, str]], str]:
    """
    Parser for legacy Excel (.xls):
    Supports ZIP-based .xlsx renamed as .xls, HTML/XML table exports saved as .xls,
    optional xlrd if installed, and pure-Python BIFF8 ASCII + UTF-16LE stream extraction.
    """
    if not raw_bytes:
        return [], ""
    data = bytes(raw_bytes)

    # 1. ZIP header -> actually .xlsx saved with .xls extension
    if data[:4] == b"PK\x03\x04":
        return extract_links_from_xlsx_bytes(data)

    # 2. HTML table / SpreadsheetML XML exported as .xls
    head = data[:512].lstrip().lower()
    if head.startswith((b"<html", b"<!doctype", b"<table", b"<?xml", b"<workbook", b"\xef\xbb\xbf<")):
        text = decode_text_bytes(data)
        if text:
            return extract_links_from_html_content(text)

    raw_pairs: List[Tuple[str, str]] = []

    # 3. Optional xlrd if available in environment
    try:
        import xlrd  # type: ignore
        wb = xlrd.open_workbook(file_contents=data)
        for sheet in wb.sheets():
            for link_obj in getattr(sheet, "hyperlink_list", []) or []:
                url = getattr(link_obj, "url_or_path", None)
                if url:
                    raw_pairs.extend(extract_links_from_text_chunk(str(url)))
            for r in range(sheet.nrows):
                for c in range(sheet.ncols):
                    val = sheet.cell_value(r, c)
                    if isinstance(val, str) and val:
                        for line in val.splitlines():
                            raw_pairs.extend(extract_links_from_text_chunk(line))
        if raw_pairs:
            return _deduplicate_pairs(raw_pairs), ""
    except Exception:
        pass

    # 4. Pure-Python BIFF8 binary scan: ASCII runs + UTF-16LE runs (both even & odd offsets)
    for run in re.findall(rb"[\x20-\x7e]{15,}", data):
        decoded = run.decode("ascii", errors="ignore")
        if any(tok in decoded.lower() for tok in ("magnet:", "ed2k://", "thunder://", ".torrent")):
            raw_pairs.extend(extract_links_from_text_chunk(decoded))

    for offset in (0, 1):
        sliced = data[offset:]
        if len(sliced) % 2 == 1:
            sliced = sliced[:-1]
        if not sliced:
            continue
        decoded_u16 = sliced.decode("utf-16-le", errors="ignore")
        for seg in re.split(r"[\x00-\x1f\x7f-\x9f]+", decoded_u16):
            if len(seg) >= 15 and any(tok in seg.lower() for tok in ("magnet:", "ed2k://", "thunder://", ".torrent")):
                raw_pairs.extend(extract_links_from_text_chunk(seg))

    return _deduplicate_pairs(raw_pairs), ""


def parse_document_links(
    file_name: str,
    raw_bytes: bytes,
    mime_type: str = "",
) -> Tuple[List[Tuple[str, str]], str, str]:
    """
    Parse an uploaded document (.txt / .csv / .html / .htm / .xlsx / .xlsm / .xls / .torrent).

    Returns:
        (pairs_of_kind_and_link, first_non_link_line, doc_kind_label)
    Raises:
        UnicodeDecodeError: if a text document's encoding cannot be decoded.
    """
    base_name, ext = get_document_base_and_ext(file_name)
    mime = (mime_type or "").lower().strip()

    if ext == ".torrent" or mime == "application/x-bittorrent":
        return extract_magnet_from_torrent_bytes(raw_bytes, fallback_name=base_name), "", "TORRENT"

    if ext in (".xlsx", ".xlsm") or mime in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroenabled.12",
    ):
        pairs, first_line = extract_links_from_xlsx_bytes(raw_bytes)
        return pairs, first_line, "EXCEL"

    if ext == ".xls" or mime == "application/vnd.ms-excel":
        pairs, first_line = extract_links_from_xls_bytes(raw_bytes)
        return pairs, first_line, "EXCEL"

    content = decode_text_bytes(raw_bytes)
    if content is None:
        raise UnicodeDecodeError("unknown", b"", 0, 1, "无法解析文件编码")

    if ext == ".csv" or mime in ("text/csv", "application/csv"):
        pairs, first_line = extract_links_from_csv_content(content)
        return pairs, first_line, "CSV"

    if ext in (".html", ".htm") or mime in ("text/html", "application/xhtml+xml"):
        pairs, first_line = extract_links_from_html_content(content)
        return pairs, first_line, "HTML"

    pairs, first_line = extract_links_from_txt_content(content)
    return pairs, first_line, "TXT"


def build_preview_txt_filename(base_name: str, link_count: int, time_str: str) -> str:
    """
    Build preview TXT filename matching `[原文件名+数量V+时间].txt`:
    e.g. `电影合集+12V+20261125_0310.txt`.
    """
    clean_base = re.sub(r'[\\/:*?"<>|\r\n]+', "-", (base_name or "links").strip()).strip(" .")
    if not clean_base:
        clean_base = "links"
    # Keep filename within a comfortable length for Telegram/OS
    if len(clean_base) > 80:
        clean_base = clean_base[:80].rstrip(" .-")
    return f"{clean_base}+{link_count}V+{time_str}.txt"
