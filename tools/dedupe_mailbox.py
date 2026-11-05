#!/usr/bin/env python3
"""RSS由来メール(Message-ID が @rss.feed.invalid)の重複を、フォルダ内で取り除く単発ツール。

同じ Message-ID は、一番古いもの(UIDが最小)を1通残して、あとを削除する。
既定は確認だけ(削除しない)。APPLY=1 で実際に削除する。
環境変数: DATA_DIR(config.json の場所) / IMAP_HOST, IMAP_USER, IMAP_PASS / APPLY=1
引数: 対象フォルダ名(省略時は config.json の processed_folder)
"""
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import imaputil  # noqa: E402

APPLY = os.environ.get("APPLY", "0") == "1"
SUFFIX = "@rss.feed.invalid>"


def dedupe(mb, folder):
    im = mb.im
    typ, _ = im.select(mb.q(folder), readonly=not APPLY)
    if typ != "OK":
        print(f"- ⚠ {folder}: 開けません")
        return 0, 0
    typ, data = im.uid("SEARCH", None, "ALL")
    uids = data[0].split() if data and data[0] else []
    groups, total = defaultdict(list), 0
    for i in range(0, len(uids), 300):
        chunk = b",".join(uids[i:i + 300]).decode()
        typ, rows = im.uid("FETCH", chunk, "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        for row in rows or []:
            if not isinstance(row, tuple):
                continue
            mu = re.search(rb"UID (\d+)", row[0])
            mm = re.search(rb"(?i)message-id:\s*(<[^>\s]+>)", row[1])
            if mu and mm and mm.group(1).decode("ascii", "replace").lower().endswith(SUFFIX):
                groups[mm.group(1)].append(int(mu.group(1)))
                total += 1
    dup = [u for us in groups.values() for u in sorted(us)[1:]]  # 一番古い1通だけ残す
    if APPLY and dup:
        for i in range(0, len(dup), 200):
            im.uid("STORE", ",".join(map(str, dup[i:i + 200])), "+FLAGS.SILENT", r"(\Deleted)")
        im.expunge()
    print(f"- {folder}: 対象 {total}件 / 重複 {len(dup)}件" + (" → 削除しました" if APPLY and dup else " (確認のみ・削除していません)" if dup else ""))
    return total, len(dup)


def main():
    data = Path(os.environ.get("DATA_DIR", "."))
    cfg = json.loads((data / "config.json").read_text(encoding="utf-8"))
    folders = sys.argv[1:] or [cfg["processed_folder"]]
    mb = imaputil.Mailbox(imaputil.connect(cfg["account"]), dry=True)  # フォルダは作らない
    print("# 重複メールの整理" + ("（実行）" if APPLY else "（確認のみ）"))
    for f in folders:
        dedupe(mb, f)
    try:
        mb.im.logout()
    except Exception:
        pass


if __name__ == "__main__":
    main()
