# -*- coding: utf-8 -*-
"""Unit tests for >500KB TXT support, CSV/HTML/torrent parsing, and multi-link TXT preview confirmation flow."""

import asyncio
import hashlib
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT_DIR, "app")
for p in (ROOT_DIR, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import init
os.makedirs(os.path.join(ROOT_DIR, "tmp"), exist_ok=True)
init.DB_FILE = os.path.join(ROOT_DIR, "tmp", "_test_file_link_parser.db")
from telegram import ReplyParameters
from telegram.ext import Application
from app.utils.file_link_parser import (
    is_supported_link_document,
    parse_document_links,
    build_preview_txt_filename,
)
from app.handlers.download_handler import (
    handle_txt_file,
    handle_file_confirm_callback,
    handle_category_selection_global,
    register_download_handlers,
    remember_user_last_save_path,
    clear_user_last_save_path,
    DEFAULT_FILE_CONFIRM_AUTO_TIMEOUT_SEC,
    get_file_confirm_auto_timeout_sec,
    DOWNLOAD_LINK_FILTER,
    LINK_DOCUMENT_FILTER,
)
from app.handlers.menu_handler import (
    handle_unrecognized_message,
    build_help_text,
    reset_panel_states,
    get_chat_panel_state,
    schedule_bottom_panel_refresh,
)


class _DummyLogger:
    def debug(self, msg, *a, **kw): pass
    def info(self, msg, *a, **kw): pass
    def warn(self, msg, *a, **kw): pass
    def warning(self, msg, *a, **kw): pass
    def error(self, msg, *a, **kw): pass


def test_csv_html_txt_and_torrent_parsing():
    m1 = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=MovieA"
    m2 = "magnet:?xt=urn:btih:ABCDEF0123456789ABCDEF0123456789ABCDEF01"
    m3_base32 = "magnet:?xt=urn:btih:ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    t1 = "https://tracker.example.com/files/sample.torrent?pass=1"
    e1 = "ed2k://|file|Clip.mp4|1024|0123456789abcdef0123456789abcdef|/"

    # 1. CSV (with UTF-8 BOM, quoted cells, commas, magnet + torrent + ed2k + duplicates)
    csv_text = (
        "\ufeffid,title,link,backup\n"
        f'1,"电影A,含逗号","{m1}","{m1}"\n'
        f'2,种子资源,{t1},{e1}\n'
        f'3,Base32磁力,"下载: {m3_base32}",\n'
    )
    pairs, first_line, kind = parse_document_links("list.csv", csv_text.encode("utf-8"), "text/csv")
    assert kind == "CSV"
    assert first_line == ""
    urls = [u for _, u in pairs]
    assert len(urls) == 4  # duplicate m1 deduplicated
    assert m1 in urls
    assert t1 in urls
    assert e1 in urls
    assert m3_base32 in urls

    # 2. HTML (href with &amp;, duplicate anchor text, data-magnet attr, .torrent link, plain text)
    html_text = f"""<!doctype html>
    <html><head><title>合集</title></head><body>
      <a href="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&amp;dn=MovieA">{m1}</a>
      <button data-magnet="{m2}">复制磁力</button>
      <a href="{t1}">下载种子</a>
      <p>纯文本里的链接：{e1}</p>
    </body></html>
    """
    pairs_h, _, kind_h = parse_document_links("page.html", html_text.encode("utf-8"), "text/html")
    assert kind_h == "HTML"
    urls_h = [u for _, u in pairs_h]
    assert len(urls_h) == 4
    assert m1 in urls_h
    assert m2 in urls_h
    assert t1 in urls_h
    assert e1 in urls_h

    # 3. Direct .torrent file -> bencode info dict hashed into magnet
    info_bencode = b"d6:lengthi12345e4:name9:Hello.mp412:piece lengthi16384e6:pieces0:e"
    torrent_bytes = b"d4:info" + info_bencode + b"e"
    expected_hash = hashlib.sha1(info_bencode).hexdigest()
    pairs_t, _, kind_t = parse_document_links("hello.torrent", torrent_bytes, "application/x-bittorrent")
    assert kind_t == "TORRENT"
    assert len(pairs_t) == 1
    assert pairs_t[0][0] == "magnet"
    assert f"urn:btih:{expected_hash}" in pairs_t[0][1]
    assert "dn=Hello.mp4" in pairs_t[0][1]

    # 4. Preview filename format: [原文件名+数量V+时间].txt
    assert build_preview_txt_filename("精选合集", 15, "20261125_0310") == "精选合集+15V+20261125_0310.txt"
    assert build_preview_txt_filename("a/b:c", 3, "20261125_0310") == "a-b-c+3V+20261125_0310.txt"


def test_large_txt_over_500kb_and_multi_link_confirmation_flow():
    init.logger = _DummyLogger()
    init.bot_config = {
        "allowed_user": [42],
        "category_folder": [
            {"name": "movies", "display_name": "电影", "path_map": [{"name": "电影", "path": "/115/Movies"}]}
        ],
    }
    init.TEMP = os.path.join(ROOT_DIR, "tmp")
    os.makedirs(init.TEMP, exist_ok=True)
    init.pending_file_confirmations = {}
    reset_panel_states()
    clear_user_last_save_path()
    remember_user_last_save_path(42, "/115/Movies")

    fake_115 = MagicMock()
    fake_115.get_quota_info.return_value = {"count": 5000, "used": 100}
    init.openapi_115 = fake_115

    # Build a >600KB TXT file containing 3 valid links + padding comments
    m1 = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    m2 = "magnet:?xt=urn:btih:fedcba9876543210fedcba9876543210fedcba98"
    t1 = "https://example.com/demo.torrent"
    big_txt = ("合集标题\n" + m1 + "\n" + m2 + "\n" + t1 + "\n" + ("# padding line\n" * 45000)).encode("utf-8")
    assert len(big_txt) > 600_000  # Well above old 500KB limit!

    fake_tg_file = MagicMock()
    fake_tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(big_txt))

    sent_docs = []

    async def _capture_send_document(**kwargs):
        # Read file content before temp file gets removed in finally block
        doc_obj = kwargs["document"]
        content = doc_obj.read().decode("utf-8")
        sent_docs.append({
            "filename": kwargs["filename"],
            "caption": kwargs["caption"],
            "reply_markup": kwargs["reply_markup"],
            "reply_parameters": kwargs["reply_parameters"],
            "content": content,
        })
        return MagicMock(message_id=900)

    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=fake_tg_file)
    bot.send_document = AsyncMock(side_effect=_capture_send_document)
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=901))
    bot.set_message_reaction = AsyncMock(return_value=True)

    doc_msg = MagicMock()
    doc_msg.message_id = 321
    doc_msg.from_user = MagicMock(id=42)
    doc_msg.document = MagicMock(file_id="fid_big", file_name="大合集.txt", file_size=len(big_txt), mime_type="text/plain")
    doc_msg.forward_origin = None
    doc_msg.forward_date = None
    doc_msg.date = datetime(2026, 11, 24, 19, 10, tzinfo=timezone.utc)  # UTC+8 = 20261125_0310
    doc_msg.reply_text = AsyncMock()

    upd = MagicMock(message=doc_msg, effective_user=MagicMock(id=42), effective_chat=MagicMock(id=100))
    ctx = MagicMock(bot=bot, user_data={})

    # 1. Uploading >500KB TXT with 3 links must NOT auto-submit; must send preview TXT [原文件名+数量V+时间].txt!
    with patch("app.handlers.download_handler.download_executor.submit") as mock_submit:
        asyncio.run(handle_txt_file(upd, ctx))
        mock_submit.assert_not_called()

    assert len(sent_docs) == 1
    preview = sent_docs[0]
    assert preview["filename"] == "大合集+3V+20261125_0310.txt"
    assert m1 in preview["content"]
    assert m2 in preview["content"]
    assert t1 in preview["content"]
    assert isinstance(preview["reply_parameters"], ReplyParameters)
    assert preview["reply_parameters"].message_id == 321

    cbs = [btn.callback_data for row in preview["reply_markup"].inline_keyboard for btn in row]
    go_cb = next(cb for cb in cbs if cb.startswith("fconfirm:go:"))
    assert any(cb.startswith("fconfirm:cat:") for cb in cbs)
    assert any(cb.startswith("fconfirm:cancel:") for cb in cbs)

    # 2. Clicking "确认开始下载" (fconfirm:go:<id>) submits the 3 links and edits document caption (NOT edit_message_text)
    q = MagicMock()
    q.data = go_cb
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_caption = AsyncMock()
    q.message = MagicMock(chat_id=100, message_id=900, text=None, caption=preview["caption"])
    cb_upd = MagicMock(callback_query=q, effective_user=MagicMock(id=42), effective_chat=MagicMock(id=100))

    with patch("app.handlers.download_handler.download_executor.submit") as mock_submit:
        asyncio.run(handle_file_confirm_callback(cb_upd, ctx))
        mock_submit.assert_called_once()
        submitted_links = mock_submit.call_args.args[1]
        assert len(submitted_links) == 3
        assert mock_submit.call_args.args[2] == "/115/Movies"

    q.edit_message_text.assert_not_awaited()
    q.edit_message_caption.assert_awaited_once()
    assert "已为您添加 3 个下载任务到队列" in q.edit_message_caption.await_args.kwargs["caption"]

    # 3. Single-link CSV file (len == 1) does NOT ask for preview confirmation; submits directly when default path exists
    sent_docs.clear()
    one_csv = f"title,url\n单条,{t1}\n".encode("utf-8")
    fake_tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(one_csv))
    doc_msg.document = MagicMock(file_id="fid_csv1", file_name="single.csv", file_size=len(one_csv), mime_type="text/csv")
    with patch("app.handlers.download_handler.download_executor.submit") as mock_submit:
        asyncio.run(handle_txt_file(upd, ctx))
        mock_submit.assert_called_once()
    assert len(sent_docs) == 0  # single link goes directly without preview TXT confirmation

    # 4. Multi-link HTML cancel flow does NOT start download
    html_multi = f'<a href="{m1}">1</a><a href="{m2}">2</a>'.encode("utf-8")
    fake_tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(html_multi))
    doc_msg.document = MagicMock(file_id="fid_html", file_name="bookmarks.html", file_size=len(html_multi), mime_type="text/html")
    with patch("app.handlers.download_handler.download_executor.submit") as mock_submit:
        asyncio.run(handle_txt_file(upd, ctx))
        mock_submit.assert_not_called()
    assert len(sent_docs) == 1
    assert sent_docs[0]["filename"] == "bookmarks+2V+20261125_0310.txt"
    cancel_cb = next(
        btn.callback_data
        for row in sent_docs[0]["reply_markup"].inline_keyboard
        for btn in row
        if btn.callback_data.startswith("fconfirm:cancel:")
    )
    q_cancel = MagicMock(data=cancel_cb, answer=AsyncMock(), edit_message_caption=AsyncMock(), message=MagicMock(chat_id=100, message_id=905, text=None))
    with patch("app.handlers.download_handler.download_executor.submit") as mock_submit:
        asyncio.run(handle_file_confirm_callback(MagicMock(callback_query=q_cancel, effective_user=MagicMock(id=42)), ctx))
        mock_submit.assert_not_called()
    assert "已取消" in q_cancel.edit_message_caption.await_args.kwargs["caption"]
    clear_user_last_save_path()


def _build_minimal_xlsx_bytes(m_shared: str, m_inline: str, m_formula: str, t_rel: str) -> bytes:
    """Create a valid minimal .xlsx archive in memory exercising sharedStrings, inlineStr, formula, and .rels hyperlinks."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "xl/sharedStrings.xml",
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<si><t>标题行</t></si>'
            f'<si><t>{m_shared.replace("&", "&amp;")}</t></si>'
            f'<si><t>点击下载种子</t></si>'
            f'</sst>',
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>'
            f'<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
            f'<row r="2"><c r="A2" t="s"><v>1</v></c></row>'
            f'<row r="3"><c r="A3" t="inlineStr"><is><t>{m_inline}</t></is></c></row>'
            f'<row r="4"><c r="A4" t="str"><f>HYPERLINK("{m_formula}","下载")</f><v>下载</v></c></row>'
            f'<row r="5"><c r="A5" t="s"><v>2</v></c></row>'
            f'</sheetData></worksheet>',
        )
        zf.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target="{t_rel.replace("&", "&amp;")}" TargetMode="External"/>'
            f'</Relationships>',
        )
    return buf.getvalue()


def test_htm_and_excel_xlsx_xls_parsing_and_confirm():
    m1 = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=SheetShared"
    m2 = "magnet:?xt=urn:btih:fedcba9876543210fedcba9876543210fedcba98"
    m3 = "magnet:?xt=urn:btih:1111222233334444555566667777888899990000"
    t1 = "https://tracker.example.com/dl/excel_link.torrent?k=1"

    # 1. .htm file
    htm_bytes = f'<html><body><a href="{m1.replace("&", "&amp;")}">链接1</a><p>{t1}</p></body></html>'.encode("utf-8")
    pairs_htm, _, kind_htm = parse_document_links("index.htm", htm_bytes, "text/html")
    assert kind_htm == "HTML"
    assert [u for _, u in pairs_htm] == [m1, t1]

    # 2. .xlsx file (sharedStrings + inlineStr + HYPERLINK formula + external .rels hyperlink)
    xlsx_bytes = _build_minimal_xlsx_bytes(m1, m2, m3, t1)
    pairs_xlsx, _, kind_xlsx = parse_document_links("资源表.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert kind_xlsx == "EXCEL"
    urls_xlsx = [u for _, u in pairs_xlsx]
    assert len(urls_xlsx) == 4
    assert m1 in urls_xlsx and m2 in urls_xlsx and m3 in urls_xlsx and t1 in urls_xlsx

    # 3. Binary legacy .xls (BIFF8-style ASCII + UTF-16LE strings at odd/even offset)
    xls_binary = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + b"\x00\x00" + m2.encode("ascii") + b"\x00\x00\x01"
        + m3.encode("utf-16-le") + b"\x00\x00"
        + t1.encode("utf-16-le") + b"\x00\x00"
    )
    pairs_xls, _, kind_xls = parse_document_links("old_sheet.xls", xls_binary, "application/vnd.ms-excel")
    assert kind_xls == "EXCEL"
    urls_xls = [u for _, u in pairs_xls]
    assert m2 in urls_xls and m3 in urls_xls and t1 in urls_xls

    # 4. HTML table exported with .xls extension
    xls_html = f"<html><table><tr><td>{m1}</td><td>{t1}</td></tr></table></html>".encode("utf-8")
    pairs_xls_h, _, kind_xls_h = parse_document_links("export.xls", xls_html, "application/vnd.ms-excel")
    assert kind_xls_h == "EXCEL"
    assert [u for _, u in pairs_xls_h] == [m1, t1]

    # 5. End-to-end handle_txt_file with multi-link .xlsx triggers preview TXT `[资源表+4V+时间].txt` confirmation
    init.logger = _DummyLogger()
    init.bot_config = {"allowed_user": [42], "category_folder": []}
    init.TEMP = os.path.join(ROOT_DIR, "tmp")
    os.makedirs(init.TEMP, exist_ok=True)
    init.pending_file_confirmations = {}
    clear_user_last_save_path()
    remember_user_last_save_path(42, "/115/Movies")
    fake_115 = MagicMock()
    fake_115.get_quota_info.return_value = {"count": 5000, "used": 10}
    init.openapi_115 = fake_115

    captured = []
    async def _cap_doc(**kwargs):
        captured.append((kwargs["filename"], kwargs["document"].read().decode("utf-8")))
        return MagicMock(message_id=950)

    fake_tg_file = MagicMock(download_as_bytearray=AsyncMock(return_value=bytearray(xlsx_bytes)))
    bot = MagicMock(
        get_file=AsyncMock(return_value=fake_tg_file),
        send_document=AsyncMock(side_effect=_cap_doc),
        set_message_reaction=AsyncMock(return_value=True),
    )
    msg = MagicMock(
        message_id=500,
        from_user=MagicMock(id=42),
        document=MagicMock(
            file_id="fid_xlsx",
            file_name="资源表.xlsx",
            file_size=len(xlsx_bytes),
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        forward_origin=None,
        forward_date=None,
        date=datetime(2026, 11, 24, 19, 20, tzinfo=timezone.utc),
        reply_text=AsyncMock(),
    )
    with patch("app.handlers.download_handler.download_executor.submit") as mock_submit:
        asyncio.run(handle_txt_file(
            MagicMock(message=msg, effective_user=MagicMock(id=42), effective_chat=MagicMock(id=100)),
            MagicMock(bot=bot, user_data={}),
        ))
        mock_submit.assert_not_called()
    assert len(captured) == 1
    assert captured[0][0] == "资源表+4V+20261125_0320.txt"
    assert m1 in captured[0][1] and t1 in captured[0][1]
    clear_user_last_save_path()


def test_filters_handler_order_and_unrecognized_skip_for_csv_html():
    init.logger = _DummyLogger()
    init.bot_config = {"allowed_user": [42]}

    for fname, mime in [
        ("a.txt", "text/plain"),
        ("b.csv", "text/csv"),
        ("c.html", "text/html"),
        ("d.HTM", "text/html"),
        ("e.torrent", "application/x-bittorrent"),
        ("f.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("g.xlsm", "application/vnd.ms-excel.sheet.macroenabled.12"),
        ("h.xls", "application/vnd.ms-excel"),
    ]:
        msg = MagicMock(
            message_id=10,
            text=None,
            caption="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
            document=MagicMock(file_name=fname, mime_type=mime),
            entities=[],
            caption_entities=[],
            photo=[],
        )
        assert is_supported_link_document(msg.document) is True
        assert LINK_DOCUMENT_FILTER.filter(msg) is True
        assert DOWNLOAD_LINK_FILTER.filter(msg) is False  # must not be hijacked by text/caption filter

        bot = MagicMock()
        bot.send_message = AsyncMock()
        asyncio.run(handle_unrecognized_message(
            MagicMock(message=msg, effective_user=MagicMock(id=42), effective_chat=MagicMock(id=100)),
            MagicMock(bot=bot, user_data={}),
        ))
        bot.send_message.assert_not_awaited()

    # Handler registration order: fconfirm callback MUST precede catch-all handle_category_selection_global
    app = Application.builder().token("123456:TESTTOKEN").build()
    register_download_handlers(app)
    g0_callbacks = [getattr(h, "callback", None) for h in app.handlers[0]]
    assert g0_callbacks.index(handle_file_confirm_callback) < g0_callbacks.index(handle_category_selection_global)

    help_text = build_help_text()
    assert "500KB" not in help_text or "无 500KB 限制" in help_text
    assert ".csv" in help_text and ".html" in help_text


def test_concurrent_two_txt_files_category_selection_isolated():
    """Sending 2 TXT files back-to-back and picking categories for both in any order must never lose links."""
    init.logger = _DummyLogger()
    init.bot_config = {
        "allowed_user": [42],
        "category_folder": [
            {
                "name": "movies",
                "display_name": "电影",
                "path_map": [
                    {"name": "外语电影", "path": "/115/Movies/Foreign"},
                    {"name": "华语电影", "path": "/115/Movies/Chinese"},
                ],
            },
            {
                "name": "av",
                "display_name": "AV",
                "path_map": [{"name": "默认AV", "path": "/115/AV"}],
            },
        ],
    }
    init.TEMP = os.path.join(ROOT_DIR, "tmp")
    os.makedirs(init.TEMP, exist_ok=True)
    init.pending_file_confirmations = {}
    reset_panel_states()
    clear_user_last_save_path()

    fake_115 = MagicMock()
    fake_115.get_quota_info.return_value = {"count": 5000, "used": 10}
    init.openapi_115 = fake_115

    f1_bytes = (
        "magnet:?xt=urn:btih:1111111111111111111111111111111111111111\n"
        "magnet:?xt=urn:btih:2222222222222222222222222222222222222222\n"
    ).encode("utf-8")
    f2_bytes = (
        "magnet:?xt=urn:btih:3333333333333333333333333333333333333333\n"
        "magnet:?xt=urn:btih:4444444444444444444444444444444444444444\n"
        "magnet:?xt=urn:btih:5555555555555555555555555555555555555555\n"
    ).encode("utf-8")

    preview_markups = []
    cat_messages = []

    async def _cap_doc(**kwargs):
        preview_markups.append(kwargs["reply_markup"])
        return MagicMock(message_id=1000 + len(preview_markups))

    async def _cap_msg(**kwargs):
        cat_messages.append(kwargs)
        return MagicMock(message_id=2000 + len(cat_messages))

    bot = MagicMock()
    bot.send_document = AsyncMock(side_effect=_cap_doc)
    bot.send_message = AsyncMock(side_effect=_cap_msg)
    bot.set_message_reaction = AsyncMock(return_value=True)
    ctx = MagicMock(bot=bot, user_data={})

    # 1. Send BOTH TXT files before confirming either
    for idx, (fname, data, msg_id) in enumerate([("File1.txt", f1_bytes, 101), ("File2.txt", f2_bytes, 102)], 1):
        bot.get_file = AsyncMock(return_value=MagicMock(download_as_bytearray=AsyncMock(return_value=bytearray(data))))
        msg = MagicMock(
            message_id=msg_id,
            from_user=MagicMock(id=42),
            document=MagicMock(file_id=f"fid_{idx}", file_name=fname, file_size=len(data), mime_type="text/plain"),
            forward_origin=None,
            forward_date=None,
            date=datetime(2026, 11, 25, 7, 50, tzinfo=timezone.utc),
            reply_text=AsyncMock(),
        )
        asyncio.run(handle_txt_file(MagicMock(message=msg, effective_user=MagicMock(id=42), effective_chat=MagicMock(id=100)), ctx))

    assert len(preview_markups) == 2

    def _first_cb(markup, prefix):
        return next(btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data.startswith(prefix))

    cat_cb_1 = _first_cb(preview_markups[0], "fconfirm:cat:")
    cat_cb_2 = _first_cb(preview_markups[1], "fconfirm:cat:")

    # 2. User clicks "确认并选择保存目录" on BOTH File 1 AND File 2 before picking a folder!
    for cb in (cat_cb_1, cat_cb_2):
        q = MagicMock(data=cb, answer=AsyncMock(), edit_message_caption=AsyncMock(), message=MagicMock(chat_id=100, message_id=1001, text=None))
        asyncio.run(handle_file_confirm_callback(MagicMock(callback_query=q, effective_user=MagicMock(id=42)), ctx))

    assert len(cat_messages) == 2
    # Both category pickers carry distinct fcat:<cid>:... callbacks
    main_cb_1_movies = cat_messages[0]["reply_markup"].inline_keyboard[0][0].callback_data
    main_cb_2_av = cat_messages[1]["reply_markup"].inline_keyboard[1][0].callback_data
    assert main_cb_1_movies.startswith("fcat:")
    assert main_cb_2_av.startswith("fcat:")
    assert main_cb_1_movies.split(":")[1] != main_cb_2_av.split(":")[1]

    submitted = []
    with patch("app.handlers.download_handler.download_executor.submit", side_effect=lambda fn, *a: submitted.append(a)):
        # 3. Pick File 1 main category "电影" (2 sub-categories -> edits to fsub buttons)
        q_m1 = MagicMock(data=main_cb_1_movies, answer=AsyncMock(), edit_message_text=AsyncMock(), message=MagicMock(chat_id=100, message_id=2001, text="pick"))
        asyncio.run(handle_file_confirm_callback(MagicMock(callback_query=q_m1, effective_user=MagicMock(id=42)), ctx))
        sub_markup_1 = q_m1.edit_message_text.await_args.kwargs["reply_markup"]
        sub_cb_1_chinese = sub_markup_1.inline_keyboard[1][0].callback_data
        assert sub_cb_1_chinese.startswith("fsub:")

        # 4. Finish File 1 sub-category -> submits 2 links to /115/Movies/Chinese
        q_s1 = MagicMock(data=sub_cb_1_chinese, answer=AsyncMock(), edit_message_text=AsyncMock(), message=MagicMock(chat_id=100, message_id=2001, text="pick sub"))
        asyncio.run(handle_file_confirm_callback(MagicMock(callback_query=q_s1, effective_user=MagicMock(id=42)), ctx))

        # 5. Now finish File 2 ("AV", 1 sub-category -> auto-submits 3 links to /115/AV without losing links!)
        q_m2 = MagicMock(data=main_cb_2_av, answer=AsyncMock(), edit_message_text=AsyncMock(), message=MagicMock(chat_id=100, message_id=2002, text="pick 2"))
        asyncio.run(handle_file_confirm_callback(MagicMock(callback_query=q_m2, effective_user=MagicMock(id=42)), ctx))

    assert len(submitted) == 2
    # Submission 1: File1 (2 links -> /115/Movies/Chinese, origin msg 101)
    assert len(submitted[0][0]) == 2
    assert submitted[0][1] == "/115/Movies/Chinese"
    assert submitted[0][4] == [(100, 101)]
    # Submission 2: File2 (3 links -> /115/AV, origin msg 102)
    assert len(submitted[1][0]) == 3
    assert submitted[1][1] == "/115/AV"
    assert submitted[1][4] == [(100, 102)]
    sent_text_2 = q_m2.edit_message_text.await_args.args[0] if q_m2.edit_message_text.await_args.args else q_m2.edit_message_text.await_args.kwargs.get("text", "")
    assert "未找到下载链接数据" not in sent_text_2
    assert "已为您添加 3 个下载任务到队列" in sent_text_2
    clear_user_last_save_path()


def test_default_folder_one_click_confirm_and_debounced_panel_burst():
    """
    1. Even on a fresh session (no prior path chosen), multi-link file confirmation immediately offers
       '✅ 确认下载至默认文件夹' + '📂 确认并下载至其他文件夹', and 1-click go submits without a second popup.
    2. Sending 3 TXT files in rapid succession refreshes the bottom control panel ONLY ONCE.
    """
    init.logger = _DummyLogger()
    old_db = init.DB_FILE
    test_db = os.path.join(ROOT_DIR, "tmp", "test_default_folder_debounce.db")
    os.makedirs(os.path.join(ROOT_DIR, "tmp"), exist_ok=True)
    if os.path.exists(test_db):
        os.remove(test_db)
    init.DB_FILE = test_db

    try:
        init.bot_config = {
            "allowed_user": [42],
            "category_folder": [
                {
                    "name": "movies",
                    "display_name": "电影",
                    "path_map": [
                        {"name": "默认电影", "path": "/115/DefaultMovies"},
                        {"name": "其他电影", "path": "/115/OtherMovies"},
                    ],
                }
            ],
        }
        init.TEMP = os.path.join(ROOT_DIR, "tmp")
        init.pending_file_confirmations = {}
        reset_panel_states()
        clear_user_last_save_path()

        fake_115 = MagicMock()
        fake_115.get_quota_info.return_value = {"count": 5000, "used": 10}
        init.openapi_115 = fake_115

        data = (
            "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            "magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
        ).encode("utf-8")

        async def _scenario():
            state = get_chat_panel_state(100)
            state["panel_message_id"] = 50  # Simulate active bottom control panel

            sent_docs = []
            panel_sends = []

            async def _cap_doc(**kwargs):
                sent_docs.append(kwargs)
                return MagicMock(message_id=1100 + len(sent_docs))

            async def _cap_msg(**kwargs):
                panel_sends.append(kwargs)
                return MagicMock(message_id=2100 + len(panel_sends))

            bot = MagicMock()
            bot.get_file = AsyncMock(return_value=MagicMock(download_as_bytearray=AsyncMock(return_value=bytearray(data))))
            bot.send_document = AsyncMock(side_effect=_cap_doc)
            bot.send_message = AsyncMock(side_effect=_cap_msg)
            bot.delete_message = AsyncMock(return_value=True)
            bot.set_message_reaction = AsyncMock(return_value=True)
            ctx = MagicMock(bot=bot, user_data={})

            # Shorten debounce delay to 0.08s for fast deterministic test
            orig_sched = schedule_bottom_panel_refresh
            with patch(
                "app.handlers.menu_handler.schedule_bottom_panel_refresh",
                side_effect=lambda b, cid, delay=None: orig_sched(b, cid, delay=0.08),
            ):
                # Send 3 TXT files in rapid succession
                for idx in range(1, 4):
                    msg = MagicMock(
                        message_id=300 + idx,
                        from_user=MagicMock(id=42),
                        document=MagicMock(file_id=f"f{idx}", file_name=f"Batch{idx}.txt", file_size=len(data), mime_type="text/plain"),
                        forward_origin=None,
                        forward_date=None,
                        date=datetime(2026, 11, 25, 8, 5, tzinfo=timezone.utc),
                        reply_text=AsyncMock(),
                    )
                    await handle_txt_file(
                        MagicMock(message=msg, effective_user=MagicMock(id=42), effective_chat=MagicMock(id=100)),
                        ctx,
                    )
                    await asyncio.sleep(0.01)

                # Wait for single debounced refresh to fire
                await asyncio.sleep(0.15)

            # Bottom panel must have been re-posted ONLY ONCE for all 3 files!
            assert len(sent_docs) == 3
            assert len(panel_sends) == 1
            assert bot.delete_message.await_count == 1
            assert bot.delete_message.await_args.kwargs["message_id"] == 50

            # Check button labels on preview doc #1 (no prior path remembered -> falls back to /115/DefaultMovies)
            markup0 = sent_docs[0]["reply_markup"]
            labels = [btn.text for row in markup0.inline_keyboard for btn in row]
            cbs = [btn.callback_data for row in markup0.inline_keyboard for btn in row]
            assert "✅ 确认下载至默认文件夹" in labels
            assert "📂 确认并下载至其他文件夹" in labels
            assert "⏸ 取消30秒自动倒计时" in labels
            assert any(cb.startswith("fconfirm:stop_timer:") for cb in cbs)
            assert "/115/DefaultMovies" in sent_docs[0]["caption"]
            go_cb = next(cb for cb in cbs if cb.startswith("fconfirm:go:"))

            # Clicking '✅ 确认下载至默认文件夹' starts download immediately with NO secondary popup
            q_go = MagicMock(
                data=go_cb,
                answer=AsyncMock(),
                edit_message_caption=AsyncMock(),
                edit_message_text=AsyncMock(),
                message=MagicMock(chat_id=100, message_id=1101, text=None),
            )
            with patch("app.handlers.download_handler.download_executor.submit") as mock_submit:
                await handle_file_confirm_callback(
                    MagicMock(callback_query=q_go, effective_user=MagicMock(id=42)),
                    ctx,
                )
                mock_submit.assert_called_once()
                assert mock_submit.call_args.args[2] == "/115/DefaultMovies"
            q_go.edit_message_caption.assert_awaited_once()
            assert "已为您添加 2 个下载任务到队列" in q_go.edit_message_caption.await_args.kwargs["caption"]

        asyncio.run(_scenario())
        clear_user_last_save_path()
    finally:
        init.DB_FILE = old_db
        if os.path.exists(test_db):
            os.remove(test_db)


def test_confirm_30s_auto_timeout_and_stop_timer_button():
    """
    1. Default timeout is 30s; if user does not pick a folder in time, auto-submits to the default folder.
    2. Clicking '⏸ 取消倒计时' cancels ONLY the auto-download countdown while preserving manual confirmation buttons.
    """
    init.logger = _DummyLogger()
    old_db = init.DB_FILE
    test_db = os.path.join(ROOT_DIR, "tmp", "test_confirm_30s_timeout.db")
    os.makedirs(os.path.join(ROOT_DIR, "tmp"), exist_ok=True)
    if os.path.exists(test_db):
        os.remove(test_db)
    init.DB_FILE = test_db

    try:
        init.bot_config = {
            "allowed_user": [42],
            "category_folder": [
                {
                    "name": "movies",
                    "display_name": "电影",
                    "path_map": [{"name": "默认电影", "path": "/115/DefaultMovies"}],
                }
            ],
        }
        assert DEFAULT_FILE_CONFIRM_AUTO_TIMEOUT_SEC == 30.0
        assert get_file_confirm_auto_timeout_sec() == 30.0

        init.bot_config["file_confirm_auto_timeout_sec"] = 0.06
        assert get_file_confirm_auto_timeout_sec() == 0.06

        init.TEMP = os.path.join(ROOT_DIR, "tmp")
        init.pending_file_confirmations = {}
        reset_panel_states()
        clear_user_last_save_path()

        fake_115 = MagicMock()
        fake_115.get_quota_info.return_value = {"count": 5000, "used": 10}
        init.openapi_115 = fake_115

        data = (
            "magnet:?xt=urn:btih:1234567890abcdef1234567890abcdef12345678\n"
            "magnet:?xt=urn:btih:abcdef1234567890abcdef1234567890abcdef12\n"
        ).encode("utf-8")

        async def _scenario():
            sent_docs = []

            async def _cap_doc(**kwargs):
                sent_docs.append(kwargs)
                return MagicMock(message_id=1200 + len(sent_docs))

            bot = MagicMock()
            bot.get_file = AsyncMock(return_value=MagicMock(download_as_bytearray=AsyncMock(return_value=bytearray(data))))
            bot.send_document = AsyncMock(side_effect=_cap_doc)
            bot.send_message = AsyncMock(return_value=MagicMock(message_id=2200))
            bot.edit_message_caption = AsyncMock(return_value=True)
            bot.set_message_reaction = AsyncMock(return_value=True)
            ctx = MagicMock(bot=bot, user_data={})

            def _make_doc_update(msg_id, fname):
                msg = MagicMock(
                    message_id=msg_id,
                    from_user=MagicMock(id=42),
                    document=MagicMock(file_id=f"fid_{msg_id}", file_name=fname, file_size=len(data), mime_type="text/plain"),
                    forward_origin=None,
                    forward_date=None,
                    date=datetime(2026, 11, 25, 8, 20, tzinfo=timezone.utc),
                    reply_text=AsyncMock(),
                )
                return MagicMock(message=msg, effective_user=MagicMock(id=42), effective_chat=MagicMock(id=100))

            # Part A: No user click -> auto-downloads to default folder after timeout
            with patch("app.handlers.download_handler.download_executor.submit") as mock_submit:
                await handle_txt_file(_make_doc_update(401, "AutoGo.txt"), ctx)
                mock_submit.assert_not_called()
                await asyncio.sleep(0.15)
                mock_submit.assert_called_once()
                assert mock_submit.call_args.args[2] == "/115/DefaultMovies"
                assert len(mock_submit.call_args.args[1]) == 2

            bot.edit_message_caption.assert_awaited_once()
            assert "自动确认下载至默认文件夹" in bot.edit_message_caption.await_args.kwargs["caption"]
            assert len(init.pending_file_confirmations) == 0

            # Part B: User clicks '⏸ 取消倒计时' -> auto-download does NOT fire after timeout, manual buttons remain!
            with patch("app.handlers.download_handler.download_executor.submit") as mock_submit2:
                await handle_txt_file(_make_doc_update(402, "StopTimer.txt"), ctx)
                markup_b = sent_docs[-1]["reply_markup"]
                cbs_b = [btn.callback_data for row in markup_b.inline_keyboard for btn in row]
                stop_cb = next(cb for cb in cbs_b if cb.startswith("fconfirm:stop_timer:"))
                go_cb = next(cb for cb in cbs_b if cb.startswith("fconfirm:go:"))

                q_stop = MagicMock(
                    data=stop_cb,
                    answer=AsyncMock(),
                    edit_message_caption=AsyncMock(),
                    edit_message_text=AsyncMock(),
                    message=MagicMock(chat_id=100, message_id=1202, text=None),
                )
                await handle_file_confirm_callback(
                    MagicMock(callback_query=q_stop, effective_user=MagicMock(id=42)),
                    ctx,
                )

                # Wait longer than timeout: auto-download must NOT trigger!
                await asyncio.sleep(0.15)
                mock_submit2.assert_not_called()

                stop_caption = q_stop.edit_message_caption.await_args.kwargs["caption"]
                stop_markup = q_stop.edit_message_caption.await_args.kwargs["reply_markup"]
                stop_cbs = [btn.callback_data for row in stop_markup.inline_keyboard for btn in row]
                assert "已取消自动倒计时" in stop_caption
                assert not any(cb.startswith("fconfirm:stop_timer:") for cb in stop_cbs)
                assert go_cb in stop_cbs
                assert any(cb.startswith("fconfirm:cat:") for cb in stop_cbs)
                assert any(cb.startswith("fconfirm:cancel:") for cb in stop_cbs)

                # Part C: User manually confirms later -> still submits cleanly!
                q_go = MagicMock(
                    data=go_cb,
                    answer=AsyncMock(),
                    edit_message_caption=AsyncMock(),
                    edit_message_text=AsyncMock(),
                    message=MagicMock(chat_id=100, message_id=1202, text=None),
                )
                await handle_file_confirm_callback(
                    MagicMock(callback_query=q_go, effective_user=MagicMock(id=42)),
                    ctx,
                )
                mock_submit2.assert_called_once()
                assert mock_submit2.call_args.args[2] == "/115/DefaultMovies"

        asyncio.run(_scenario())
        clear_user_last_save_path()
    finally:
        init.DB_FILE = old_db
        if os.path.exists(test_db):
            os.remove(test_db)


if __name__ == "__main__":
    test_csv_html_txt_and_torrent_parsing()
    test_large_txt_over_500kb_and_multi_link_confirmation_flow()
    test_htm_and_excel_xlsx_xls_parsing_and_confirm()
    test_filters_handler_order_and_unrecognized_skip_for_csv_html()
    test_concurrent_two_txt_files_category_selection_isolated()
    test_default_folder_one_click_confirm_and_debounced_panel_burst()
    test_confirm_30s_auto_timeout_and_stop_timer_button()
    print("All file-link parser (>500KB TXT, CSV, HTML/HTM, Excel, torrent) and preview confirmation tests passed!")
