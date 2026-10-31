"""除外メールに「除外理由」を埋め込む。

- ヘッダー X-Exclusion-Reason に理由を入れる
- text/plain, text/html の本文先頭に理由のバナーを挿入する(添付は触らない)
"""
import email
import email.header
import html
import re


def banner(reason: str) -> str:
    return f"【除外理由】{reason}"


def _decode(part):
    payload = part.get_payload(decode=True) or b""
    cs = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(cs, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


def annotate_raw(raw: bytes, reason: str) -> bytes:
    msg = email.message_from_bytes(raw)
    del msg["X-Exclusion-Reason"]
    msg["X-Exclusion-Reason"] = email.header.Header(reason, "utf-8")
    text_banner = banner(reason)
    div = ('<div style="background:#fff3cd;border:1px solid #e0c36a;padding:6px 10px;'
           'margin:0 0 12px;font:13px sans-serif;color:#5c4a00">'
           + html.escape(text_banner) + "</div>")
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ct = part.get_content_type()
        if ct == "text/plain":
            new = f"{text_banner}\n{'-' * 30}\n\n{_decode(part)}"
        elif ct == "text/html":
            t = _decode(part)
            new, n = re.subn(r"(<body[^>]*>)", lambda m: m.group(1) + div, t, count=1, flags=re.I)
            if not n:
                new = div + t
        else:
            continue
        del part["Content-Transfer-Encoding"]
        part.set_payload(new, "utf-8")
    return msg.as_bytes()
