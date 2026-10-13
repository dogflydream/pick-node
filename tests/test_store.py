"""结果仓库测试: 筛选/排序/导出规则."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.store import ResultStore, is_potentially_usable, is_strictly_usable
from core.util import setup_console

setup_console()

store = ResultStore(ROOT / "results.json")
print(f"载入: {store.path.name}  错误={store.error!r}")

rows = store.all()
print(f"总条目: {len(rows)}")

s = store.summary()
print("\n--- summary ---")
for k in ("total", "reachable", "google_ok", "usable", "strict_usable",
          "gemini_available", "gemini_likely", "gemini_blocked",
          "antigravity_available", "avg_latency", "min_latency"):
    print(f"  {k:24} = {s[k]}")
print(f"  countries (top5)         = {list(s['countries'].items())[:5]}")
print(f"  protocols                = {s['protocols']}")

print("\n--- facets ---")
f = store.facets()
print(f"  countries: {len(f['countries'])} 个")
print(f"  gemini   : {f['gemini']}")

# ---- 筛选 ----
print("\n--- 筛选测试 ---")
jp = store.query(country="JP")
print(f"  country=JP            -> {len(jp)} 条 (summary 里 JP={s['countries'].get('JP')})")
assert len(jp) == s["countries"].get("JP"), "JP 筛选数与统计不一致"

hy = store.query(protocol="HYSTERIA2")
print(f"  protocol=HYSTERIA2    -> {len(hy)} 条 (summary={s['protocols'].get('HYSTERIA2')})")
assert len(hy) == s["protocols"].get("HYSTERIA2")

hk = store.query(country="HK", gemini="blocked")
print(f"  HK + gemini=blocked   -> {len(hk)} 条")
assert all(r["gemini"] == "blocked" for r in hk)

srch = store.query(search="日本")
print(f"  search='日本'         -> {len(srch)} 条")
assert all("日本" in r["tag"] for r in srch)

uo = store.query(usable_only=True)
print(f"  usable_only           -> {len(uo)} 条")
assert len(uo) == s["usable"], f"usable_only {len(uo)} != summary.usable {s['usable']}"

# ---- 排序 ----
print("\n--- 排序测试 ---")
by_lat = store.query(sort="latency")
lats = [r["latency"] for r in by_lat if r.get("latency")]
print(f"  sort=latency 前5       -> {lats[:5]}")
reach_lats = [r["latency"] for r in by_lat if r.get("reachable") and r.get("latency")]
assert reach_lats == sorted(reach_lats), "可达节点延迟排序应升序"
# 不可达的信息节点(个位数毫秒)不能排在前面
assert by_lat[0]["reachable"], "延迟排序首个应为可达节点"
assert by_lat[0]["latency"] >= 100, \
    f"首个延迟 {by_lat[0]['latency']}ms 过小, 疑似噪声节点未排除"
print(f"  [OK] 首个为可达节点, 延迟 {by_lat[0]['latency']}ms")

dflt = store.query(sort="default")
print(f"  sort=default 前3可用性 -> {[is_potentially_usable(r) for r in dflt[:3]]}")
assert is_potentially_usable(dflt[0]), "默认排序首个应为潜在可用"

# ---- 导出 (核心需求) ----
print("\n--- 导出测试 ---")
uris = store.export_uris("usable")
print(f"  scope=usable  -> {len(uris)} 条 URI")
assert len(set(uris)) == len(uris), "导出的 URI 不应重复"
assert all(u.startswith(("vless://", "vmess://", "trojan://", "ss://",
                         "hysteria2://", "hy2://", "tuic://")) for u in uris), \
    "导出内容必须是合法 URI 行"
assert len(uris) == s["usable"], f"导出数 {len(uris)} != usable {s['usable']}"

strict = store.export_uris("strict")
print(f"  scope=strict  -> {len(strict)} 条")
assert len(strict) == s["strict_usable"]

allu = store.export_uris("all")
print(f"  scope=all     -> {len(allu)} 条 (unique)")
assert len(allu) <= len(rows)

# 勾选导出
pick = [r["node_id"] for r in store.all()[:5]]
sel = store.export_uris("usable", ids=pick)
print(f"  ids=前5个     -> {len(sel)} 条")
assert len(sel) <= 5

# 关键反例: 被 blocked / 不可达的节点绝不能出现在 usable 导出里
usable_ids = set()
for r in store.all():
    if is_potentially_usable(r):
        usable_ids.add(r["node_id"])
for r in store.all():
    if r["node_id"] in usable_ids:
        continue
    assert r["node"] not in uris, f"不可用节点混入导出: {r['tag'][:30]}"
print("  [OK] 所有 blocked/unavailable/不可达节点均未混入导出")

print("\nALL STORE TESTS PASSED")
