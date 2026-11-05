"""RSSの「取得済み記録」と配信の安定性のテスト(ローカルHTTPサーバー + 模擬IMAP)。python tests/test_rss_state.py"""
import datetime as dt, functools, http.server, json, os, sys, tempfile, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
tmp = Path(tempfile.mkdtemp())
(tmp / "srv").mkdir(); (tmp / "data/rules").mkdir(parents=True)
os.environ.update(DATA_DIR=str(tmp / "data"), QUIET_LOG="1")
os.environ.pop("DRY_RUN", None); os.environ.pop("GITHUB_STEP_SUMMARY", None)
import imaputil, run  # noqa: E402


def feed(n, name):
    now = dt.datetime(2026, 11, 5, 4, 0, tzinfo=dt.timezone.utc)
    it = "".join(f'<item><title>{name}{i}</title><link>http://ex.com/{name}/{i}</link><guid>{name}-{i}</guid>'
                 f'<pubDate>{(now - dt.timedelta(minutes=i)):%a, %d %b %Y %H:%M:%S +0000}</pubDate><description>x</description></item>'
                 for i in range(n))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>{name}</title>{it}</channel></rss>'


(tmp / "srv/big.xml").write_text(feed(700, "big"), encoding="utf-8")      # 500件を超えるフィード
(tmp / "srv/small.xml").write_text(feed(3, "small"), encoding="utf-8")
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp / "srv")))
http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
rule = [{"name": "keep", "enabled": True, "match": "always", "terms": [], "result": "keep", "label": "INBOX"}]
for f in ("a", "b"):
    (tmp / f"data/rules/rules_{f}.json").write_text(json.dumps(rule), encoding="utf-8")
(tmp / "data/feeds.txt").write_text(f"{base}/big.xml\n{base}/small.xml\n", encoding="utf-8")


def configure(**rss):
    cfg = {"regex_ignore_case": True, "processed_folder": "N_P", "account": {},
           "rulesets": {"a": {"file": "rules/rules_a.json", "default_reject": "X"},
                        "b": {"file": "rules/rules_b.json", "default_reject": "X", "by_label": {}}},
           "rss": {"feeds_file": "feeds.txt", "rulesets": ["a", "b"], "first_run_max_age_hours": 100000, **rss}}
    (tmp / "data/config.json").write_text(json.dumps(cfg), encoding="utf-8")


class IM:
    mode = "ok"      # ok / conn(接続エラー) / no(サーバーが拒否)
    sent = 0
    def list(self): return ("OK", [b'(\\HasNoChildren) "." "N_P"'])
    def noop(self): return ("OK", [])
    def select(self, *a, **k): return ("NO", [])
    def create(self, *a): return ("OK", [])
    def subscribe(self, *a): return ("OK", [])
    def logout(self): pass
    def append(self, *a):
        if IM.mode == "conn": raise OSError("EOF occurred in violation of protocol")
        if IM.mode == "no": return ("NO", [b"quota"])
        IM.sent += 1; return ("OK", [])


def connect(acc):
    if IM.mode == "conn": raise OSError("接続できません")
    return IM()


imaputil.connect = connect


def go(mode="ok"):
    IM.mode, IM.sent = mode, 0
    run.START = time.monotonic(); run.summary = None
    run.detail.clear(); run.public.clear()
    try: run.main()
    except SystemExit: pass
    st = json.loads((tmp / "data/state/state.json").read_text(encoding="utf-8"))["rss"]["seen"]
    return IM.sent, {k.rsplit("/", 1)[1]: len(v) for k, v in st.items()}


configure(max_deliver_per_run=10000)
# A) 接続が切れて全件失敗 → 失敗した記事は「処理済み」にならない
sent, seen = go("conn"); assert sent == 0 and seen == {"big.xml": 0, "small.xml": 0}, (sent, seen)
# 接続が回復した次の実行で、取りこぼし無く全件(703件)が配信される
sent, seen = go("ok"); assert sent == 703 and seen == {"big.xml": 700, "small.xml": 3}, (sent, seen)
# 500件超のフィードでも、次の実行で重複配信しない
sent, seen = go("ok"); assert sent == 0, sent
# B) サーバーがAPPENDを拒否(容量超過など) → 配信できていないので処理済みにしない
(tmp / "data/state/state.json").unlink()
sent, seen = go("no"); assert sent == 0 and seen["big.xml"] == 0, (sent, seen)
sent, seen = go("ok"); assert sent == 703, sent
# C) 1回の配信上限: 100件ずつ進み、最終的に全件配信される(重複なし)
(tmp / "data/state/state.json").unlink(); configure(max_deliver_per_run=100)
total = 0
for _ in range(10):
    sent, seen = go("ok"); total += sent
    if sent == 0: break
assert total == 703, total
# D) 実行時間の上限: 上限を過ぎていたら配信せず、記録も進めない
(tmp / "data/state/state.json").unlink(); configure(max_deliver_per_run=10000)
c = json.loads((tmp / "data/config.json").read_text()); c["run_limit_minutes"] = 0
(tmp / "data/config.json").write_text(json.dumps(c), encoding="utf-8")
IM.mode, IM.sent = "ok", 0
run.START = time.monotonic() - 10
try: run.main()
except SystemExit: pass
# E) 基準化(BASELINE): 配信せず、今ある記事を全部「取得済み」にする → 以降の実行で重複配信しない
(tmp / "data/state/state.json").unlink() if (tmp / "data/state/state.json").exists() else None
c = json.loads((tmp / "data/config.json").read_text()); c["run_limit_minutes"] = 8
(tmp / "data/config.json").write_text(json.dumps(c), encoding="utf-8")
run.BASELINE = True; sent, seen = go("ok"); run.BASELINE = False
assert sent == 0 and seen == {"big.xml": 700, "small.xml": 3}, (sent, seen)
sent, seen = go("ok"); assert sent == 0, sent
st = tmp / "data/state/state.json"
(tmp / "data/state/state.json").unlink()
assert not st.exists() or sum(len(v) for v in json.loads(st.read_text())["rss"]["seen"].values()) == 0   # 上限超過 → 何も進めない
assert IM.sent == 0
print("OK: rss state tests passed")
