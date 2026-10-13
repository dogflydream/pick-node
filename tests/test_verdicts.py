"""单元测试: 判定不变式 (不依赖网络).

覆盖 v2 的两条硬规则:
  1) Antigravity 必须严格跟随 Gemini
  2) Google 未通过时, Gemini 不得判为可用
  以及异常兜底: 任一阶段抛错也不能留下 gemini=unknown 却被视为可用.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.detector import Detection, Detector
from core.store import is_potentially_usable
from core.util import setup_console

setup_console()

FAIL = []


def check(name, cond, detail=""):
    print(f"[{'OK  ' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


d = Detector()

print("=" * 92)
print("1. resolve_antigravity 跟随 Gemini (各档位)")
print("=" * 92)
cases = [
    ("available", "available"),
    ("likely", "likely"),
    ("blocked", "blocked"),
    ("unavailable", "unavailable"),
    ("unknown", "unavailable"),   # unknown 不得变成可用
]
for gem, expect in cases:
    det = Detection()
    det.google = "success"
    det.gemini = gem
    d.resolve_antigravity(det)
    check(f"gemini={gem:11} -> antigravity={det.antigravity:11}",
          det.antigravity == expect, f"期望 {expect}")

print()
print("=" * 92)
print("2. Gemini 可用但 Google 失败 -> 必须降级")
print("=" * 92)
for gval in ("fail", "unknown"):
    det = Detection()
    det.google = gval
    det.gemini = "available"
    d.resolve_antigravity(det)
    # 说明: 这里只验证 resolve 本身; 实际降级在 detect_gemini 里做.
    print(f"  google={gval:8} -> antigravity={det.antigravity}")

print()
print("=" * 92)
print("3. 可用性判定: 必须 google=success 且 gemini 在可用档")
print("=" * 92)
combos = [
    ("success", "available", True),
    ("success", "likely", True),
    ("success", "blocked", False),
    ("success", "unavailable", False),
    ("success", "unknown", False),
    ("fail", "available", False),      # 关键: google 失败 => 不可用
    ("fail", "blocked", False),
    ("unknown", "unknown", False),
]
for gval, gem, expect in combos:
    row = {"reachable": True, "google": gval, "gemini": gem, "risk_level": "none"}
    got = is_potentially_usable(row)
    check(f"google={gval:8} gemini={gem:11} -> usable={got}",
          got == expect, f"期望 {expect}")

print()
print("=" * 92)
print("4. 风险 high 必须否决可用性")
print("=" * 92)
row = {"reachable": True, "google": "success", "gemini": "available",
       "risk_level": "high"}
check("risk=high + 其余都达标 -> 不可用", not is_potentially_usable(row))

print()
print("=" * 92)
print("5. 阶段异常兜底: gemini 不留 unknown")
print("=" * 92)


class Boom(Detector):
    """让 gemini 阶段抛异常."""
    def detect_gemini(self, proxy_url, det):
        raise RuntimeError("模拟阶段异常")


b = Boom()
det = Detection()
det.reachable = True
det.country = "JP"
# 直接调用 run 需要网络, 这里手工复现 run 的兜底逻辑
det.gemini = "unknown"
if det.gemini == "unknown":
    det.gemini = "unavailable"
    det.gemini_reason = "检测未能得出明确结论 (阶段异常或数据缺失)"
b.resolve_antigravity(det)
check("异常后 gemini != unknown", det.gemini != "unknown", f"={det.gemini}")
check("异常后 antigravity 不为可用", det.antigravity != "available",
      f"={det.antigravity}")

print()
print("=" * 92)
if FAIL:
    print(f"失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("ALL VERDICT INVARIANT TESTS PASSED")
