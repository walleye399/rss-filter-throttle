"""python tests/test_filterlib.py  (ダミーのルールだけを使うテスト。実データは不要)"""
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from filterlib import Msg, evaluate_chain, parse_dat
from imaputil import utf7_encode
from annotate import annotate_raw
import email, email.header

rules = parse_dat((ROOT / "tests/fixtures/sample.dat").read_text(encoding="utf-8"))
assert len(rules) == 5 and rules[-1]["match"] == "always" and rules[-1]["result"] == "keep"
RS = [{"tag": "a", "rules": rules, "default_reject": "X_Excluded", "by_label": {"Sent": "B_ExcludedBlogs"}}]
ev = lambda **kw: evaluate_chain(RS, Msg(**kw), True)

d = ev(subject="新サービスの提供開始について")
assert d["result"] == "reject" and d["folder"] == "X_Excluded" and "件名が「について」で終わる" in d["reason"], d
assert "「rtx 50」が一致" in ev(subject="新作 rtx 5090 登場")["reason"]           # 大文字小文字を区別しない
assert evaluate_chain(RS, Msg(subject="rtx 5090"), False)["result"] == "keep"        # 区別する設定ならkeep
assert ev(subject="親子で学ぶ防災")["result"] == "reject" and ev(subject="親子でお出かけ")["result"] == "keep"
d = ev(subject="普通の記事", content_base="http://example-matome.blog.jp/1")
assert d["folder"] == "B_ExcludedBlogs" and "example-matome" in d["reason"], d     # 移動先 Sent → B_ExcludedBlogs
assert ev(subject="質問です", from_="Sample Feed")["result"] == "reject"
assert ev(subject="質問です", from_="Other")["result"] == "keep"
assert ev(subject="今日の天気は晴れ")["result"] == "keep"
assert utf7_encode("INBOX") == "INBOX" and utf7_encode("A&B") == "A&-B"
raw = b"From: a@x\r\nSubject: =?utf-8?b?44OG44K544OI?=\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nhello"
m = email.message_from_bytes(annotate_raw(raw, "フィルタa「R」: 件名に「x」を含む"))
assert "【除外理由】" in m.get_payload(decode=True).decode() and m["X-Exclusion-Reason"]
print("OK: all tests passed")
