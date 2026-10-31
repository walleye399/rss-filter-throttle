"""メールの加工: 除外理由の埋め込み と 青い帯のリンク(件名→本文中のリンク)の挿入。

- 除外理由: ヘッダー X-Exclusion-Reason、text/plain・text/html の本文先頭のバナー
- リンク帯: text/html の本文先頭に、白文字・青い帯・+3pt のリンク(RSSメッセージと同じ見た目)
添付ファイルは変更しません。
"""
import email
import email.header
import html
import re
from html.parser import HTMLParser

LINK_STYLE = ("display:inline-block;background:#2b3a55;color:#ffffff;text-decoration:none;"
              "font-size:1.3em;font-size:calc(1em + 3pt);padding:4px 10px;border-radius:4px")
REASON_STYLE = ("background:#fff3cd;border:1px solid #e0c36a;padding:6px 10px;"
                "margin:0 0 12px;font:13px sans-serif;color:#5c4a00")

BROWSER_RE = re.compile(r"ブラウザ|ウェブ版|web版|webで|view\s+(this\s+)?(email|e-mail|message|mail)?\s*(in|on)?\s*(your\s+)?(web\s+)?browser"
                        r"|view\s+online|view\s+in\s+browser|online\s+version|read\s+online|表示されない", re.I)
SKIP_RE = re.compile(r"配信停止|配信解除|購読解除|解除|unsubscribe|opt[- ]?out|email\s*preferences|メール配信設定", re.I)


def banner(reason: str) -> str:
    return f"【除外理由】{reason}"


def _decode(part):
    payload = part.get_payload(decode=True) or b""
    cs = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(cs, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href.strip(), "".join(self._text).strip()))
            self._href = None


def pick_link(html_text: str):
    """「ブラウザで見る」系のリンク → 無ければ、配信停止などを除いた最初のリンク。"""
    p = _Anchors()
    try:
        p.feed(html_text)
    except Exception:
        pass
    cands = [(h, t) for h, t in p.links if re.match(r"https?://", h, re.I) and not SKIP_RE.search(t + " " + h)]
    for h, t in cands:
        if BROWSER_RE.search(t):
            return h
    return cands[0][0] if cands else None


def find_link(raw: bytes):
    msg = email.message_from_bytes(raw)
    for part in msg.walk():
        if part.get_content_type() == "text/html" and part.get_content_disposition() != "attachment":
            url = pick_link(_decode(part))
            if url:
                return url
    return None


def annotate_raw(raw: bytes, reason=None, subject=None, url=None) -> bytes:
    """reason があれば除外理由を、subject と url があれば青い帯のリンクを埋め込む。"""
    msg = email.message_from_bytes(raw)
    if reason:
        del msg["X-Exclusion-Reason"]
        msg["X-Exclusion-Reason"] = email.header.Header(reason, "utf-8")
    html_pre = ""
    if reason:
        html_pre += f'<div style="{REASON_STYLE}">{html.escape(banner(reason))}</div>'
    if subject and url:
        html_pre += (f'<p><a href="{html.escape(url)}" style="{LINK_STYLE}">'
                     f'<font color="#ffffff">{html.escape(subject or "(無題)")}</font></a></p>')
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ct = part.get_content_type()
        if ct == "text/plain" and reason:
            new = f"{banner(reason)}\n{'-' * 30}\n\n{_decode(part)}"
        elif ct == "text/html" and html_pre:
            t = _decode(part)
            new, n = re.subn(r"(<body[^>]*>)", lambda m: m.group(1) + html_pre, t, count=1, flags=re.I)
            if not n:
                new = html_pre + t
        else:
            continue
        del part["Content-Transfer-Encoding"]
        part.set_payload(new, "utf-8")
    return msg.as_bytes()
