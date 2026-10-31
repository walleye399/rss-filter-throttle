#!/usr/bin/env python3
"""使い方: python tools/convert_rules.py msgFilterRules_a.dat rules/rules_a.json"""
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from filterlib import parse_dat

src, dst = sys.argv[1], sys.argv[2]
rules = parse_dat(open(src, encoding="utf-8").read())
json.dump(rules, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
n_terms = sum(len(r["terms"]) for r in rules)
print(f"{src}: {len(rules)} ルール / {n_terms} 条件 -> {dst}")
