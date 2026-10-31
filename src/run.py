#!/usr/bin/env python3
"""RSS とメールマガジンをフィルタールールで振り分ける（さくらのIMAP 1アカウント内で完結）。

  RSS : feeds.txt の各フィード -> フィルタ a → b -> 該当しなければ N_ProcessedNewsletters、
        該当すれば除外フォルダへ(除外理由つき)
  メール: INBOX の全メッセージ -> フィルタ a -> 同上。INBOX は処理後に空になる
  後片付け: 除外フォルダ内の古いメッセージ(既定24時間)を削除

環境変数: CONFIG(既定 config.json) / DRY_RUN=1 で書き込み・削除・状態更新を一切しない
"""
import concurrent.futures as cf
import datetime as dt
import email
import email.header
import email.utils
import hashlib
import html
import imaplib
import json
import os
import re
import sys
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imaputil  # noqa: E402
from annotate import LINK_STYLE, REASON_STYLE, annotate_raw, banner, find_link  # noqa: E402
from filterlib import Msg, evaluate_chain  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent          # プログラム(公開リポジトリ)
DATA = Path(os.environ.get("DATA_DIR", str(ROOT)))   # ルール・feeds.txt・状態・ログ(非公開リポジトリ)
QUIET = os.environ.get("QUIET_LOG", "0") == "1"      # 1: 公開してよい行(件数など)だけをActionsのログに出す
DRY = os.environ.get("DRY_RUN", "0") == "1"
STATE_PATH = DATA / "state" / "state.json"
SEEN_CAP = 500
UA = "Mozilla/5.0 (compatible; tb-migrate/2.0)"
detail, public = [], []  # detail: 非公開ログ(logs/last_run.md)用 / public: Actionsのログ・Summary用


def log(line=""):
    """詳細(件名・差出人・除外理由・URL等)。QUIET時はActionsのログに出さず、非公開ログにだけ残す。"""
    detail.append(line)
    if not QUIET:
        print(line, flush=True)
        public.append(line)


def pub(line=""):
    """公開しても問題ない行(件数・フォルダ名・エラー有無)。"""
    detail.append(line)
    print(line, flush=True)
    public.append(line)


def fail(name, e):
    log(f"\n❌ {name} が失敗: {e!r}")
    if QUIET:
        pub(f"\n❌ {name} が失敗 ({type(e).__name__})")


# ---------------------------------------------------------------- 共通
def load_state():
    return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}


def save_state(state):
    if DRY:
        return
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")


def dh(v):
    if v is None:
        return ""
    v = str(v)
    try:  # 生の8bitヘッダー(RFC2047でないUTF-8)への保険
        v = v.encode("ascii", "surrogateescape").decode("utf-8", "replace")
    except UnicodeError:
        pass
    try:
        return str(email.header.make_header(email.header.decode_header(v)))
    except Exception:
        return v


def strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or ""))


def build_rulesets(cfg, names):
    out = []
    for n in names:
        rs = cfg["rulesets"][n]
        rules = json.loads((DATA / rs["file"]).read_text(encoding="utf-8"))
        out.append({"tag": n, "rules": rules, "default_reject": rs["default_reject"],
                    "by_label": rs.get("by_label", {})})
    return out


# ---------------------------------------------------------------- RSS
def read_feeds(path):
    urls = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            urls.append(s)
    return urls


CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


def fetch_browser(url):
    """bot判定でブロックされるサイト用: ブラウザ相当のヘッダー + Chrome風のTLS通信(curl_cffi)。"""
    u = urlparse(url)
    hdr = {"Accept": "application/rss+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.7",
           "Accept-Language": "ja,en;q=0.8", "Referer": f"{u.scheme}://{u.netloc}/"}
    try:
        from curl_cffi import requests as creq
    except ImportError:
        creq = None
    if creq:
        r = creq.get(url, headers=hdr, impersonate="chrome", timeout=25)
    else:  # curl_cffi が無い場合は通常の requests でヘッダーだけ変える
        r = requests.get(url, headers={**hdr, "User-Agent": CHROME_UA}, timeout=25)
    r.raise_for_status()
    return r.content


def fetch_feed(url, browser_hosts=()):
    try:
        if urlparse(url).hostname in browser_hosts:
            content = fetch_browser(url)
        else:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
            r.raise_for_status()
            content = r.content
        return url, feedparser.parse(content), None
    except Exception as e:  # 1フィードの失敗で全体を止めない
        return url, None, str(e)


def entry_dt(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return dt.datetime(*t[:6], tzinfo=dt.timezone.utc) if t else None


def build_rss_message(e, feed_title, link, reason=None):
    title = html.unescape(e.get("title", "") or "(無題)")
    author = e.get("author") or feed_title
    content = (e["content"][0].get("value", "") if e.get("content") else "") or e.get("summary", "")
    eid = e.get("id") or link or title
    m = EmailMessage()
    m["Subject"] = title
    m["From"] = email.utils.formataddr((author, "rss@feed.invalid"))
    m["To"] = "rss@feed.invalid"
    d = entry_dt(e) or dt.datetime.now(dt.timezone.utc)
    m["Date"] = email.utils.format_datetime(d)
    m["Message-ID"] = f"<{hashlib.sha1(eid.encode()).hexdigest()}@rss.feed.invalid>"
    m["X-Feed"] = feed_title
    pre_t = pre_h = ""
    if reason:
        m["X-Exclusion-Reason"] = reason
        pre_t = f"{banner(reason)}\n{'-' * 30}\n\n"
        pre_h = f'<div style="{REASON_STYLE}">' + html.escape(banner(reason)) + "</div>"
    m.set_content(pre_t + strip_html(content) + (f"\n\n{link}" if link else ""))
    # タイトルのリンク: 白文字・濃い帯・文字サイズ +3pt（calc未対応のクライアントは 1.3em にフォールバック）
    link_style = LINK_STYLE
    m.add_alternative(f'{pre_h}<p><a href="{html.escape(link or "")}" style="{link_style}">'
                      f'<font color="#ffffff">{html.escape(title)}</font></a></p>{content}',
                      subtype="html")
    if link:  # set_content() が Content-* ヘッダーを消すので最後に付ける
        m["Content-Base"] = link
    return m, d


def run_rss(cfg, mb, state):
    c = cfg["rss"]
    rulesets = build_rulesets(cfg, c["rulesets"])
    icase = cfg.get("regex_ignore_case", False)
    keep_folder = cfg["processed_folder"]
    urls = read_feeds(DATA / c["feeds_file"])
    seen = state.setdefault("rss", {}).setdefault("seen", {})
    max_age = dt.timedelta(hours=c.get("first_run_max_age_hours", 24))
    now = dt.datetime.now(dt.timezone.utc)
    pub(f"\n## RSS ({len(urls)}フィード)")

    stats = {"keep": 0, "reject": 0, "skip_old": 0, "err": 0}
    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda u: fetch_feed(u, tuple(c.get("browser_hosts", []))), urls))
    for n, (url, d, err) in enumerate(results, 1):
        if err or d is None:
            stats["err"] += 1
            log(f"- ⚠ 取得失敗: {url} ({err})")
            if QUIET:
                pub(f"- ⚠ 取得失敗: feeds.txt の {n} 番目のフィード")
            continue
        ftitle = d.feed.get("title") or url
        first = url not in seen
        old = seen.get(url, [])
        cur_ids, todo = [], []
        for e in d.entries:
            link = e.get("link", "")
            eid = e.get("id") or link or e.get("title", "")
            cur_ids.append(eid)
            if eid in old:
                continue
            ed = entry_dt(e)
            if first and ed and now - ed > max_age:
                stats["skip_old"] += 1
                continue
            todo.append((ed or now, eid, e, link))
        done = []
        for _, eid, e, link in sorted(todo, key=lambda x: x[0]):
            title = html.unescape(e.get("title", ""))
            body_fn = lambda e=e: strip_html((e.get("content") or [{}])[0].get("value", "")
                                             or e.get("summary", ""))
            msg = Msg(subject=title, from_=f"{ftitle} {e.get('author', '')}",
                      content_base=link, body=body_fn)
            r = evaluate_chain(rulesets, msg, icase)
            try:
                if r["result"] == "keep":
                    if not DRY:
                        m, mdt = build_rss_message(e, ftitle, link)
                        mb.im.append(mb.q(keep_folder), None,
                                     imaplib.Time2Internaldate(mdt.timestamp()), m.as_bytes())
                    stats["keep"] += 1
                    log(f"- ✅ [{ftitle}] {title}")
                else:
                    if not DRY:
                        m, _ = build_rss_message(e, ftitle, link, r["reason"])
                        # 日付は付けない = 追加した時刻が保管期限の起点になる
                        mb.im.append(mb.q(r["folder"]), r"(\Seen)", None, m.as_bytes())
                    stats["reject"] += 1
                    log(f"- 🚫 [{ftitle}] {title} → {r['folder']} ← {r['reason']} (フィルタ{r['tag']})")
                done.append(eid)
            except Exception as ex_:
                stats["err"] += 1
                log(f"- ⚠ 配信失敗(次回再試行): {title} ({ex_})")
        if not DRY:
            merged = done + [i for i in cur_ids if i not in done]
            merged += [i for i in old if i not in merged]
            seen[url] = merged[:SEEN_CAP]
    pub(f"\n結果: 残す {stats['keep']} / 除外 {stats['reject']} / 初回スキップ(古い) {stats['skip_old']} / エラー {stats['err']}")


# ---------------------------------------------------------------- メール
def run_mail(cfg, mb):
    c = cfg["mail"]
    rulesets = build_rulesets(cfg, c["rulesets"])
    icase = cfg.get("regex_ignore_case", False)
    keep_folder = cfg["processed_folder"]
    im = mb.im
    pub(f"\n## メール ({c.get('source_folder', 'INBOX')})")
    typ, _ = im.select(mb.q(c.get("source_folder", "INBOX")), readonly=DRY)
    if typ != "OK":
        raise RuntimeError("受信フォルダを開けません")
    typ, data = im.uid("SEARCH", None, "ALL")
    uids = data[0].split() if data and data[0] else []
    stats = {"keep": 0, "reject": 0, "err": 0}
    changed = False
    for u in uids:
        uid = u.decode()
        typ, d = im.uid("FETCH", uid, "(FLAGS INTERNALDATE BODY.PEEK[])")
        if typ != "OK" or not d or not isinstance(d[0], tuple):
            stats["err"] += 1
            continue
        raw = d[0][1]
        meta = d[0][0].decode("ascii", "replace") + " " + " ".join(
            x.decode("ascii", "replace") for x in d[1:] if isinstance(x, bytes))
        fm = re.search(r"FLAGS \(([^)]*)\)", meta)
        dm = re.search(r'INTERNALDATE "([^"]+)"', meta)
        flags = [f for f in (fm.group(1).split() if fm else []) if f.lower() not in ("\\recent", "\\deleted")]
        flag_str = "(" + " ".join(flags) + ")" if flags else None
        idate = f'"{dm.group(1)}"' if dm else None
        m = email.message_from_bytes(raw)
        subj, frm = dh(m["Subject"]), dh(m["From"])

        def body_fn(m=m):
            texts = []
            for part in m.walk():
                if part.is_multipart() or part.get_content_disposition() == "attachment":
                    continue
                ct = part.get_content_type()
                if ct in ("text/plain", "text/html"):
                    pl = part.get_payload(decode=True) or b""
                    try:
                        s = pl.decode(part.get_content_charset() or "utf-8", "replace")
                    except LookupError:
                        s = pl.decode("utf-8", "replace")
                    texts.append(strip_html(s) if ct == "text/html" else s)
            return "\n".join(texts)

        r = evaluate_chain(rulesets, Msg(subject=subj, from_=frm, body=body_fn), icase)
        url = find_link(raw)  # 本文中の「ブラウザで見る」系リンク(無ければ最初のリンク)
        try:
            if r["result"] == "keep":
                stats["keep"] += 1
                log(f"- ✅ {frm} / {subj}" + (f"\n  🔗 {url}" if url else ""))
                if not DRY:
                    if url:  # 青い帯のリンクを入れて入れ直す(未読などのフラグ・受信日時は引き継ぐ)
                        typ, _ = im.append(mb.q(keep_folder), flag_str, idate,
                                           annotate_raw(raw, subject=subj, url=url))
                        if typ != "OK":
                            raise RuntimeError("APPEND失敗")
                    else:
                        typ, _ = im.uid("COPY", uid, mb.q(keep_folder))  # フラグ(未読)は保持される
                        if typ != "OK":
                            raise RuntimeError("COPY失敗")
                    im.uid("STORE", uid, "+FLAGS.SILENT", r"(\Deleted)")
                    changed = True
            else:
                stats["reject"] += 1
                log(f"- 🚫 {frm} / {subj} → {r['folder']} ← {r['reason']} (フィルタ{r['tag']})")
                if not DRY:
                    try:
                        new_raw = annotate_raw(raw, r["reason"], subj, url)
                    except Exception as ex_:  # 埋め込み失敗時はヘッダー追記だけで保存
                        log(f"  (本文への理由埋め込みに失敗: {ex_!r})")
                        hdr = "X-Exclusion-Reason: " + str(email.header.Header(r["reason"], "utf-8")) + "\r\n"
                        new_raw = hdr.encode("ascii", "replace") + raw
                    typ, _ = im.append(mb.q(r["folder"]), r"(\Seen)", None, new_raw)
                    if typ != "OK":
                        raise RuntimeError("APPEND失敗")
                    im.uid("STORE", uid, "+FLAGS.SILENT", r"(\Deleted)")
                    changed = True
        except Exception as ex_:
            stats["err"] += 1
            log(f"  ⚠ 移動失敗(次回再試行): {ex_}")
    if changed:
        im.expunge()
    pub(f"\n結果: 残す {stats['keep']} / 除外 {stats['reject']} / エラー {stats['err']}")


# ---------------------------------------------------------------- 後片付け
def run_purge(cfg, mb):
    hours = cfg.get("retention_hours", 24)
    folders = set()
    for rs in cfg["rulesets"].values():
        folders.add(rs["default_reject"])
        folders.update(rs.get("by_label", {}).values())
    pub(f"\n## 除外フォルダの掃除 ({hours}時間より古いものを削除)")
    for f in sorted(folders):
        try:
            n = imaputil.purge_older_than(mb, f, hours, dry=DRY)
            pub(f"- {f}: {n}件" + (" (DRY RUN: 削除せず)" if DRY and n else ""))
        except Exception as e:
            log(f"- ⚠ {f}: 失敗 {e!r}")
            if QUIET:
                pub(f"- ⚠ {f}: 失敗 ({type(e).__name__})")
    # ゴミ箱: 受信日時が trash_retention_hours 時間より古いものを削除（0 / null で無効）
    th = cfg.get("trash_retention_hours", 24)
    if th:
        try:
            real = mb.find_trash(cfg.get("trash_folder"))
            protected = {"INBOX", mb.name(cfg["processed_folder"]), *(mb.name(f) for f in folders)}
            if real is None:
                pub("- ⚠ ゴミ箱フォルダが見つかりません（config.json の trash_folder で指定してください）")
            elif real in protected:
                pub("- ⚠ ゴミ箱に指定されたフォルダが受信箱・処理済み・除外フォルダと同じです。削除しません")
            else:
                n = imaputil.purge_real(mb, real, th, dry=DRY)
                pub(f"- ゴミ箱({real}): {n}件（{th}時間より古いもの）" + (" (DRY RUN: 削除せず)" if DRY and n else ""))
        except Exception as e:
            log(f"- ⚠ ゴミ箱: 失敗 {e!r}")
            if QUIET:
                pub(f"- ⚠ ゴミ箱: 失敗 ({type(e).__name__})")


# ---------------------------------------------------------------- main
def main():
    cfg = json.loads((DATA / os.environ.get("CONFIG", "config.json")).read_text(encoding="utf-8"))
    state = load_state()
    pub(f"# 実行ログ {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC" + (" (DRY RUN)" if DRY else ""))
    failed = False
    im = imaputil.connect(cfg["account"])
    mb = imaputil.Mailbox(im, dry=DRY)
    steps = []
    if cfg.get("rss"):
        steps.append(("RSS", lambda: run_rss(cfg, mb, state)))
    if cfg.get("mail"):
        steps.append(("メール", lambda: run_mail(cfg, mb)))
    steps.append(("掃除", lambda: run_purge(cfg, mb)))
    for name, fn in steps:
        try:
            fn()
        except Exception as e:
            failed = True
            fail(name, e)
    save_state(state)
    try:
        im.logout()
    except Exception:
        pass
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        Path(sp).write_text("\n".join(public) + "\n", encoding="utf-8")
    (DATA / "logs").mkdir(exist_ok=True)  # 詳細ログは非公開リポジトリ側に残す
    (DATA / "logs" / "last_run.md").write_text("\n".join(detail) + "\n", encoding="utf-8")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
