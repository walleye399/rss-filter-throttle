"""Thunderbird msgFilterRules.dat のパース＆評価ライブラリ。

対応: 標準フィルター(subject/from/body/任意ヘッダー) と FiltaQuilla の subjectRegex/bodyRegex。
評価は Thunderbird と同じく「上から順に最初に一致したルールで確定」。
"""
import re
import urllib.parse

STD_FIELDS = {"subject", "from", "body", "content-base"}
REGEX_FIELDS = {
    "filtaquilla@mesquilla.com#subjectRegex": "subject",
    "filtaquilla@mesquilla.com#bodyRegex": "body",
}
STD_OPS = {"contains", "doesn't contain", "is", "isn't", "begins with", "ends with"}
REGEX_OPS = {"matches", "doesn't match"}


def _unescape_line(v: str) -> str:
    return re.sub(r'\\(["\\])', r"\1", v)


def _parse_condition(cond: str):
    cond = _unescape_line(cond)
    if cond.strip() == "ALL":
        return "always", []
    m = re.match(r"^(AND|OR)\s", cond)
    if not m:
        raise ValueError(f"未対応の条件形式: {cond[:80]}")
    mode = "all" if m.group(1) == "AND" else "any"
    raw_terms = re.findall(r"\((.*?)\)(?= (?:AND|OR) \(|$)", cond)
    terms = []
    for t in raw_terms:
        attr, op, value = t.split(",", 2)
        attr = attr.strip().replace('\\"', "").strip('"')
        value = value.replace("%29", ")").replace('\\"', '"')
        terms.append({"field": attr, "op": op, "value": value})
    return mode, terms


def parse_dat(text: str):
    """msgFilterRules.dat のテキスト -> ルールのリスト(dict)"""
    rules, cur = [], None
    for line in text.replace("\r", "").split("\n"):
        m = re.match(r'^(\w+)="(.*)"$', line)
        if not m:
            continue
        k, v = m.groups()
        if k == "name":
            cur = {"name": v}
            rules.append(cur)
        elif cur is not None:
            cur[k] = v
    out = []
    for r in rules:
        if r.get("action") != "Move to folder":
            raise ValueError(f"未対応のアクション {r.get('action')!r} (rule={r['name']})")
        mode, terms = _parse_condition(r["condition"])
        for t in terms:
            f, op = t["field"], t["op"]
            if f in REGEX_FIELDS:
                if op not in REGEX_OPS:
                    raise ValueError(f"未対応の演算子 {op} (rule={r['name']})")
                re.compile(t["value"])  # 構文チェック
            elif f in STD_FIELDS:
                if op not in STD_OPS:
                    raise ValueError(f"未対応の演算子 {op} (rule={r['name']})")
            else:
                raise ValueError(f"未対応のフィールド {f} (rule={r['name']})")
        dest = urllib.parse.unquote(r["actionValue"])
        keep = dest.startswith("imap:")
        out.append({
            "name": r["name"],
            "enabled": r.get("enabled", "yes") == "yes",
            "match": mode,
            "terms": terms,
            "result": "keep" if keep else "reject",
            "label": dest.rstrip("/").split("/")[-1],
        })
    return out


class Msg:
    """評価対象。body は重いので callable で遅延取得。"""

    def __init__(self, subject="", from_="", content_base="", body=""):
        self.subject, self.from_, self.content_base = subject or "", from_ or "", content_base or ""
        self._body = body

    @property
    def body(self):
        if callable(self._body):
            self._body = self._body() or ""
        return self._body


FIELD_JA = {"subject": "件名", "from": "差出人", "body": "本文", "content-base": "記事URL"}


def _term_hit(term, msg: Msg, icase=False):
    """-> (一致したか, 正規表現で一致した文字列 or None)"""
    f, op, v = term["field"], term["op"], term["value"]
    if f in REGEX_FIELDS:
        text = msg.subject if REGEX_FIELDS[f] == "subject" else msg.body
        m = re.search(v, text, re.I if icase else 0)
        if op == "matches":
            return m is not None, (m.group(0) if m else None)
        return m is None, None
    text = {"subject": msg.subject, "from": msg.from_,
            "content-base": msg.content_base, "body": None}[f]
    if text is None:
        text = msg.body
    a, b = text.casefold(), v.casefold()
    ok = {
        "contains": b in a,
        "doesn't contain": b not in a,
        "is": a == b,
        "isn't": a != b,
        "begins with": a.startswith(b),
        "ends with": a.endswith(b),
    }[op]
    return ok, None


def describe_hit(term, matched=None) -> str:
    """一致した条件を日本語で説明する。"""
    f, op, v = term["field"], term["op"], term["value"]
    if f in REGEX_FIELDS:
        base = FIELD_JA[REGEX_FIELDS[f]]
        if op == "matches":
            return f"{base}(正規表現)に「{matched}」が一致"
        return f"{base}(正規表現)に一致しない [{v[:40]}]"
    base = FIELD_JA[f]
    return {
        "contains": f"{base}に「{v}」を含む",
        "doesn't contain": f"{base}に「{v}」を含まない",
        "is": f"{base}が「{v}」と一致",
        "isn't": f"{base}が「{v}」と不一致",
        "begins with": f"{base}が「{v}」で始まる",
        "ends with": f"{base}が「{v}」で終わる",
    }[op]


def evaluate_detail(rules, msg: Msg, icase=False):
    """1つのルール集合を上から評価。-> dict(result, rule, label, hits[説明文])"""
    for r in rules:
        if not r["enabled"]:
            continue
        if r["match"] == "always":
            return {"result": r["result"], "rule": r["name"], "label": r["label"], "hits": []}
        hits = []
        if r["match"] == "any":
            for t in r["terms"]:
                ok, mt = _term_hit(t, msg, icase)
                if ok:
                    hits = [describe_hit(t, mt)]
                    break
            matched = bool(hits)
        else:  # all
            matched = True
            for t in r["terms"]:
                ok, mt = _term_hit(t, msg, icase)
                if not ok:
                    matched = False
                    break
                hits.append(describe_hit(t, mt))
        if matched:
            return {"result": r["result"], "rule": r["name"], "label": r["label"], "hits": hits}
    return {"result": "keep", "rule": None, "label": None, "hits": []}


def evaluate(rules, msg: Msg, ignore_case_regex=False):
    """互換用: (result, rule_name, label)"""
    d = evaluate_detail(rules, msg, ignore_case_regex)
    return d["result"], d["rule"], d["label"]


def evaluate_chain(rulesets, msg: Msg, icase=False):
    """複数のルール集合を順に適用(例: RSSは a → b)。最初に reject したものが確定。
    rulesets: [{'tag','rules','default_reject','by_label'}]
    -> dict(result, folder, reason, tag, rule, hits)"""
    for rs in rulesets:
        d = evaluate_detail(rs["rules"], msg, icase)
        if d["result"] == "reject":
            d["tag"] = rs["tag"]
            d["folder"] = rs.get("by_label", {}).get(d["label"], rs["default_reject"])
            d["reason"] = f"フィルタ{rs['tag']}「{d['rule']}」: " + " かつ ".join(d["hits"])
            return d
    return {"result": "keep", "folder": None, "reason": "", "tag": None, "rule": None, "hits": []}
