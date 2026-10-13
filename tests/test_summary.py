"""验证 summarize() 的延迟统计修复: 不可达节点不应污染 min/avg 延迟."""
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.tester import NodeResult, summarize
from core.util import setup_console

setup_console()

raw = json.load(open("results.json", encoding="utf-8"))
results = [NodeResult(**r) for r in raw]

s = summarize(results)
print("summarize() 输出:")
for k in ("total", "usable", "google_ok", "gemini_available", "antigravity_available",
          "avg_latency_ms", "min_latency_ms"):
    print(f"  {k:22} = {s[k]}")

# 断言: 最小延迟必须来自可达节点
reachable_lats = [r.latency for r in results if r.latency > 0 and r.reachable]
all_lats = [r.latency for r in results if r.latency > 0]

print(f"\n所有节点延迟最小值   : {min(all_lats)} ms")
print(f"仅可达节点延迟最小值 : {min(reachable_lats)} ms")

assert s["min_latency_ms"] == min(reachable_lats), "min 延迟应只统计可达节点"

# 确认那个信息节点确实被排除
info = [r for r in results if r.latency == min(all_lats)]
for r in info:
    print(f"  被排除的节点: latency={r.latency} reachable={r.reachable} tag={r['tag'][:40] if isinstance(r, dict) else r.tag[:40]}")
    assert not r.reachable, "被排除的节点应当是不可达的"

# 合理性: 真实代理延迟不应低于 200ms
assert min(reachable_lats) > 200, \
    f"可达节点延迟异常低 {min(reachable_lats)}ms, 可能未真正走通"

print("\n[OK] 延迟统计正确排除不可达节点")
print(f"[OK] 可达节点延迟区间合理: {min(reachable_lats)} ~ "
      f"{max(reachable_lats)} ms")
print("\nALL SUMMARY TESTS PASSED")
