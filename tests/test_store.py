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
# 仅当数据里确实存在可用节点时才要求"首个可用";
# 某些批次 (或网络较差时的子集) 可能一个可用都没有, 那不算排序错误.
if s["usable"] > 0:
    assert is_potentially_usable(dflt[0]), "默认排序首个应为潜在可用"
    print("  [OK] 默认排序把可用节点排在前面")
else:
    print("  [跳过] 本批数据没有可用节点, 无法验证排序优先级")

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

# ---------------------------------------------------------------- 数据一致性
print()
print("=" * 92)
print("数据一致性检查 (判定规则: Google 与 Gemini 都 OK 才算可用)")
print("=" * 92)

allrows = store.all()

# 1) 不允许 google=fail 而 gemini=available (自相矛盾)
bad = [r for r in allrows
       if r.get("google") != "success"
       and r.get("gemini") in ("available", "likely")]
check_ok = len(bad) == 0
print(f"[{'OK  ' if check_ok else 'FAIL'}] 无 'google 失败但 gemini 可用' 的矛盾数据"
      f"  ({len(bad)} 个)")
assert check_ok, [r["tag"] for r in bad[:5]]

# 2) Antigravity 必须与 Gemini 完全一致 (本版本改为跟随)
diff = [r for r in allrows if r.get("gemini") != r.get("antigravity")]
check_ok = len(diff) == 0
print(f"[{'OK  ' if check_ok else 'FAIL'}] Antigravity 与 Gemini 判定完全一致"
      f"  ({len(diff)} 个不一致)")
assert check_ok, [(r["tag"], r["gemini"], r["antigravity"]) for r in diff[:5]]

# 3) 延迟指标的大小关系: rtt (小请求最小) 不应大于端到端总耗时
viol = [r for r in allrows
        if r.get("rtt_min_ms") and r.get("latency")
        and r["rtt_min_ms"] > r["latency"]]
check_ok = len(viol) == 0
print(f"[{'OK  ' if check_ok else 'FAIL'}] rtt_min_ms <= latency  ({len(viol)} 个违反)")
assert check_ok

# 4) 可用节点必须同时满足 google=success 且 gemini 在可用档
usable = [r for r in allrows if is_potentially_usable(r)]
bad2 = [r for r in usable
        if r.get("google") != "success"
        or r.get("gemini") not in ("available", "likely")]
check_ok = len(bad2) == 0
print(f"[{'OK  ' if check_ok else 'FAIL'}] 可用集合的 google/gemini 都达标"
      f"  ({len(bad2)} 个违反)")
assert check_ok

print()
print("ALL CONSISTENCY CHECKS PASSED")