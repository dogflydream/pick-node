"""发布包验收: 从干净解压目录验证核心功能可用 (不依赖开发目录)."""
import sys
from pathlib import Path

PKG = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
sys.path.insert(0, str(PKG))

from core.parser import parse_many
from core.retest import analyze_import, parse_nodes_text
from core.util import setup_console

setup_console()

print(f"验收目录: {PKG}")

# 1) 解析器
good, bad = parse_many(
    "vless://11111111-2222-3333-4444-555555555555@a.com:443?security=tls&sni=a.com#A\n"
    "hysteria2://pw@b.com:443?sni=b.com#B\n"
    "bad-line"
)
print(f"  解析器: OK={len(good)} FAIL={len(bad)}")
assert len(good) == 2 and len(bad) == 1

# 2) 导入分析 (预览 + 去重)
uri1 = "vless://22222222-2222-3333-4444-555555555555@c.com:443?security=tls&sni=c.com#C"
text = f"{uri1}\n{uri1}\nnot-a-uri"
info = analyze_import(text, set())
print(f"  导入分析: new={info['new_count']} dup={info['dup_count']} bad={info['bad_count']}")
assert info["new_count"] == 1, "批内去重失败"
assert info["dup_count"] == 1
assert info["bad_count"] == 1

# 3) 判定不变式 (评估权重最高的那条规则)
from core.detector import Detection, Detector
from core.store import is_potentially_usable

d = Detector()
det = Detection()
det.google = "success"
det.gemini = "available"
det.reachable = True
det.risk_level = "none"
assert is_potentially_usable(det.to_dict() if hasattr(det, "to_dict") else {
    "reachable": True, "google": "success", "gemini": "available",
    "risk_level": "none"}), "可用判定异常"

# Antigravity 必须跟随 Gemini
for gem, expect in (("available", "available"), ("blocked", "blocked"),
                    ("unavailable", "unavailable")):
    x = Detection()
    x.google = "success"
    x.gemini = gem
    d.resolve_antigravity(x)
    assert x.antigravity == expect, f"{gem} -> {x.antigravity}"
print("  判定不变式: Antigravity 跟随 Gemini  OK")

# 4) Web 静态资源齐全
static = PKG / "web" / "static"
for f in ("index.html", "app.js", "style.css"):
    p = static / f
    assert p.is_file() and p.stat().st_size > 500, f"缺少或不完整: {f}"
print("  Web 静态资源: index.html / app.js / style.css 齐全")

print()
print("发布包验收通过")
