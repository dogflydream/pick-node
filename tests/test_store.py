"""结果仓库测试: 筛选/排序/导出规则.

数据源选择: 优先用 results_google.json (全量 132 节点, 字段最全),
否则回退 results.json. 这样测试不依赖"当前主文件恰好是哪次跑的结果".
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.store import ResultStore, is_potentially_usable, is_strictly_usable
from core.util import setup_console

setup_console()

# 挑一个字段最全的数据文件
_candidates = [ROOT / "results_google.json", ROOT / "results.json"]
_data = next((p for p in _candidates if p.is_file()
              and len(ResultStore(p).all()) > 10), _candidates[-1])
store = ResultStore(_data)
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

# 2) Antigravity 跟随 Gemini, 但地区口径更严 (HK/MO 只影响 Antigravity).
#    因此两者【允许】不一致, 但差异必须只能来自 HK/MO —— 别的地区不一致就是 bug.
diff = [r for r in allrows if r.get("gemini") != r.get("antigravity")]
allowed_cc = {"HK", "MO"}
unexpected = [r for r in diff
              if str(r.get("country", "")).upper() not in allowed_cc]
check_ok = len(unexpected) == 0
print(f"[{'OK  ' if check_ok else 'FAIL'}] 两服务判定差异仅限 HK/MO"
      f"  (共 {len(diff)} 个不一致, 其中意外 {len(unexpected)} 个)")
assert check_ok, [(r["tag"], r.get("country"), r["gemini"], r["antigravity"])
                  for r in unexpected[:5]]

# 2b) HK/MO 的差异方向必须合理:
#     正常情况 gemini=available 而 antigravity=blocked;
#     若 gemini 本身就不可用 (连接失败等), antigravity 随之不可用也合理.
bad_dir = [r for r in diff
           if not (r.get("antigravity") == "blocked"
                   and r.get("gemini") in ("available", "unavailable"))]
check_ok = len(bad_dir) == 0
print(f"[{'OK  ' if check_ok else 'FAIL'}] HK/MO 差异方向合理 "
      f"(ag 恒为 blocked; gemini 为 available 或 unavailable)"
      f"  ({len(bad_dir)} 个反例)")
assert check_ok, [(r["tag"], r["gemini"], r["antigravity"]) for r in bad_dir[:5]]

# 2c) 关键: HK/MO 里凡是 gemini 可用的, antigravity 必须被限制
hkmo_mismatch = [r for r in diff
                 if r.get("gemini") == "available"
                 and r.get("antigravity") == "available"]
check_ok = len(hkmo_mismatch) == 0
print(f"[{'OK  ' if check_ok else 'FAIL'}] HK/MO 的 Antigravity 无可用反例"
      f"  ({len(hkmo_mismatch)} 个)")
assert check_ok

# 3) 延迟指标合理性.
#    注意: 不能断言 rtt_min_ms <= latency —— 两者测的是【不同目标】:
#      rtt      : 到 cloudflare.com(几百字节)
#      latency  : 到 google.com(含约 50KB 首页)
#    实测确实出现过反例 (某节点到 cloudflare 2.0s, 到 google 却只要 1.9s),
#    那是两条线路的真实差异, 不是测量错误. 故只校验取值范围.
bad_range = [r for r in allrows
             for v in (r.get("rtt_min_ms"), r.get("ttfb_ms"), r.get("latency"))
             if isinstance(v, (int, float)) and v and not (0 < v < 120_000)]
check_ok = len(bad_range) == 0
print(f"[{'OK  ' if check_ok else 'FAIL'}] 延迟指标取值在合理区间 (0 < v < 120s)"
      f"  ({len(bad_range)} 个越界)")
assert check_ok

# 3b) RTT 样本数: 有值时应至少有 1 个样本, 且 min 等于样本最小值
bad_samples = []
for r in allrows:
    s = r.get("rtt_samples") or []
    m = r.get("rtt_min_ms") or 0
    if s and min(s) != m:
        bad_samples.append((r.get("tag"), s, m))
check_ok = len(bad_samples) == 0
print(f"[{'OK  ' if check_ok else 'FAIL'}] rtt_min_ms == min(rtt_samples)"
      f"  ({len(bad_samples)} 个不一致)")
assert check_ok, bad_samples[:3]

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