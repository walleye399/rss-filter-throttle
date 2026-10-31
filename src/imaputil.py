import base64
import datetime as dt
import imaplib
import os
import re


def utf7_encode(s: str) -> str:
    """IMAP フォルダ名(修正UTF-7)エンコード。日本語フォルダ名用。"""
    out, buf = [], []

    def flush():
        if buf:
            b = "".join(buf).encode("utf-16-be")
            out.append("&" + base64.b64encode(b).decode().rstrip("=").replace("/", ",") + "-")
            buf.clear()

    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def q(folder: str) -> str:
    return '"' + utf7_encode(folder).replace("\\", "\\\\").replace('"', '\\"') + '"'


def connect(account: dict) -> imaplib.IMAP4_SSL:
    host = os.environ[account["host_env"]]
    user = os.environ[account["user_env"]]
    pw = os.environ[account["pass_env"]]
    port = int(account.get("port", 993))
    im = imaplib.IMAP4_SSL(host, port, timeout=60)
    im.login(user, pw)
    return im


class Mailbox:
    """フォルダ名の解決つき IMAP ラッパー。

    サーバーによっては新規フォルダが "INBOX.X_Excluded" のように INBOX 配下になるため、
    LIST で既存名を探し、無ければ作成(トップ階層→だめなら INBOX 配下)して解決する。
    dry=True のときはフォルダ作成をせず、論理名をそのまま返す。
    """

    def __init__(self, im, dry=False):
        self.im, self.dry, self.cache = im, dry, {}
        self.existing = set()
        self.sep = "."
        typ, data = im.list()
        for line in data or []:
            if not isinstance(line, bytes):
                continue
            m = re.match(rb'\((.*?)\) (?:"(.)"|NIL) (.*)$', line)
            if not m:
                continue
            if m.group(2):
                self.sep = m.group(2).decode()
            self.existing.add(m.group(3).decode("ascii", "replace").strip('"'))

    def name(self, logical: str) -> str:
        if logical.upper() == "INBOX":
            return "INBOX"
        if logical in self.cache:
            return self.cache[logical]
        enc = utf7_encode(logical)
        cands = [enc, "INBOX" + self.sep + enc]
        for c in cands:
            if c in self.existing:
                self.cache[logical] = c
                return c
        if self.dry:
            return enc
        for c in cands:
            typ, _ = self.im.create('"' + c + '"')
            if typ == "OK":
                try:
                    self.im.subscribe('"' + c + '"')
                except Exception:
                    pass
                self.existing.add(c)
                self.cache[logical] = c
                return c
        raise RuntimeError(f"フォルダを作成できません: {logical}")

    def q(self, logical: str) -> str:
        return '"' + self.name(logical) + '"'


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def purge_older_than(mb: Mailbox, logical: str, hours: float, dry=False):
    """INTERNALDATE が hours 時間より古いメッセージを削除。-> (対象数)"""
    im = mb.im
    typ, _ = im.select(mb.q(logical), readonly=dry)
    if typ != "OK":
        return 0
    typ, data = im.uid("SEARCH", None, "ALL")
    uids = data[0].split() if data and data[0] else []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    old = []
    for chunk in _chunks(uids, 500):
        typ, rows = im.uid("FETCH", b",".join(chunk).decode(), "(INTERNALDATE)")
        for row in rows or []:
            raw = row if isinstance(row, bytes) else (row[0] if row else b"")
            mu = re.search(rb"UID (\d+)", raw)
            md = re.search(rb'INTERNALDATE "([^"]+)"', raw)
            if not (mu and md):
                continue
            d = dt.datetime.strptime(md.group(1).decode().strip(), "%d-%b-%Y %H:%M:%S %z")
            if d < cutoff:
                old.append(mu.group(1).decode())
    if old and not dry:
        for chunk in _chunks(old, 200):
            im.uid("STORE", ",".join(chunk), "+FLAGS.SILENT", r"(\Deleted)")
        im.expunge()
    return len(old)
