"""python tests/test_filterlib.py  (ダミーのルールだけを使うテスト。実データは不要)"""
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from filterlib import Msg, evaluate_chain, parse_dat
from imaputil import utf7_encode
from annotate import annotate_raw, find_link, pick_link
import email, email.header

rules = parse_dat((ROOT / "tests/fixtures/sample.dat").read_text(encoding="utf-8"))
assert len(rules) == 5 and rules[-1]["match"] == "always" and rules[-1]["result"] == "keep"
RS = [{"tag": "a", "rules": rules, "default_reject": "X_Excluded", "by_label": {"Sent": "B_ExcludedBlogs"}}]
ev = lambda **kw: evaluate_chain(RS, Msg(**kw), True)

d = ev(subject="新サービスの提供開始について")
assert d["result"] == "reject" and d["folder"] == "X_Excluded" and d["reason"] == "末尾が「について」：について", d
assert ev(subject="新作 rtx 5090 登場")["reason"] == "末尾が「について」：rtx 50"           # 大文字小文字を区別しない
assert evaluate_chain(RS, Msg(subject="rtx 5090"), False)["result"] == "keep"        # 区別する設定ならkeep
assert ev(subject="親子で学ぶ防災")["reason"] == "親子の防災：親子 | 防災" and ev(subject="親子でお出かけ")["result"] == "keep"
d = ev(subject="普通の記事", content_base="http://example-matome.blog.jp/1")
assert d["folder"] == "B_ExcludedBlogs" and d["reason"] == "除外ブログ1：example-matome.blog", d     # 移動先 Sent → B_ExcludedBlogs
assert ev(subject="質問です", from_="Sample Feed")["result"] == "reject"
assert ev(subject="質問です", from_="Other")["result"] == "keep"
assert ev(subject="今日の天気は晴れ")["result"] == "keep"
assert utf7_encode("INBOX") == "INBOX" and utf7_encode("A&B") == "A&-B"
raw = b"From: a@x\r\nSubject: =?utf-8?b?44OG44K544OI?=\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nhello"
m = email.message_from_bytes(annotate_raw(raw, "R：x | y"))
assert "【除外理由】R：x | y" in m.get_payload(decode=True).decode() and m["X-Exclusion-Reason"]
# ---- リンク帯 ----
H = lambda body: ("<html><body>" + body + "</body></html>")
assert pick_link(H('<a href="https://ex.com/top">トップ</a><a href="https://ex.com/web?id=1">ブラウザで表示</a>')) == "https://ex.com/web?id=1"
assert pick_link(H('<a href="https://ex.com/unsub">配信停止</a><a href="https://ex.com/a?x=1&amp;y=2">記事はこちら</a>')) == "https://ex.com/a?x=1&y=2"
assert pick_link(H('<a href="https://ex.com/v">View in browser</a>')) == "https://ex.com/v"
assert pick_link(H('<a href="mailto:a@b.c">連絡</a><a href="https://ex.com/unsubscribe">x</a>')) is None
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
mp = MIMEMultipart("alternative"); mp["Subject"] = "件名"; mp["From"] = "a@x"
mp.attach(MIMEText("本文", "plain", "utf-8"))
mp.attach(MIMEText(H('<a href="https://ex.com/web">ブラウザで見る</a><p>本文</p>'), "html", "utf-8"))
raw = mp.as_bytes()
assert find_link(raw) == "https://ex.com/web"
assert find_link(MIMEText("text only https://ex.com", "plain", "utf-8").as_bytes()) is None
out = email.message_from_bytes(annotate_raw(raw, None, "ニュースレター 11月号", "https://ex.com/web"))
htmls = [p for p in out.walk() if p.get_content_type() == "text/html"][0].get_payload(decode=True).decode()
assert 'href="https://ex.com/web" style="display:inline-block;background:#2b3a55;color:#ffffff' in htmls and "ニュースレター 11月号" in htmls
assert "X-Exclusion-Reason" not in out and "【除外理由】" not in htmls            # 理由なし(残すメール)
out = email.message_from_bytes(annotate_raw(raw, "R：x", "件名", "https://ex.com/web"))
htmls = [p for p in out.walk() if p.get_content_type() == "text/html"][0].get_payload(decode=True).decode()
assert htmls.index("【除外理由】R：x") < htmls.index("background:#2b3a55")          # 理由 → リンク帯の順
print("OK: all tests passed")
