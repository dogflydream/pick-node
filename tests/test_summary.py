"""验证 summarize() 的延迟统计修复: 不可达节点不应污染 min/avg 延迟."""
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.tester import NodeResult, summarize
from core.util import setup_console

setup_console()

# 数据源: 优先用全量文件 (分类检测后主 results.json 可能只含少数节点)
_cands = [ROOT / "results_google.json", ROOT / "results.json"]
_src = next((p for p in _cands
             if p.is_file() and len(json.loads(
                 p.read_text(encoding="utf-8"))) > 10), _cands[-1])
print(f"数据源: {_src.name}")
raw = json.loads(_src.read_text(encoding="utf-8"))
results = [NodeResult(**r) for r in raw]

s = summarize(results)
print("summarize() 输出:")
for k in ("total", "usable", "google_ok", "gemini_available", "antigravity_available",
          "avg_latency_ms", "min_latency_ms"):
    print(f"  {k:22} = {s[k]}")

# ---- 规则验证 (用构造数据, 不依赖真实数据集恰好含什么) ----
# 规则: 统计延迟时, 排除 (1) 不可达节点 (2) 超过 120s 的超时值
from core.tester import NodeResult as _NR  # noqa: E402

synthetic = [
    # 可达 + 正常延迟
    _NR(reachable=True, latency=1200, ttfb_ms=900, rtt_min_ms=300, ok=True),
    # 可达 + 超时值 (302s) —— 不是延迟, 必须排除
    _NR(reachable=True, latency=302_000, ttfb_ms=302_000, rtt_min_ms=320),
    # 不可达 + 个位数延迟 (如"剩余流量"信息节点) —— 必须排除
    _NR(reachable=False, latency=15, ttfb_ms=15, rtt_min_ms=15),
]
ss = summarize(synthetic)
print(f"\n构造数据断言: min_latency={ss['min_latency_ms']} "
      f"min_ttfb={ss['min_ttfb_ms']} min_rtt={ss['min_rtt_ms']}")
assert ss["min_latency_ms"] == 1200, f"超时值/不可达值未排除: {ss['min_latency_ms']}"
assert ss["min_ttfb_ms"] == 900, f"超时值未排除: {ss['min_ttfb_ms']}"
assert ss["min_rtt_ms"] == 300, f"不可达值未排除: {ss['min_rtt_ms']}"
print("[OK] 统计正确排除不可达节点与超时值")

# ---- 真实数据合理性检查 ----
reachable_lats = [r.latency for r in results
                  if 0 < r.latency < 120_000 and r.reachable]
assert s["min_latency_ms"] == min(reachable_lats), "min 延迟应只统计可达且非超时的节点"
assert min(reachable_lats) > 200, \
    f"可达节点延迟异常低 {min(reachable_lats)}ms, 可能未真正走通"

print(f"[OK] 真实数据: 可达节点延迟区间 {min(reachable_lats)} ~ "
      f"{max(reachable_lats)} ms")
print("\nALL SUMMARY TESTS PASSED")
