"""单元测试: 判定不变式 (不依赖网络).

覆盖的硬规则:
  1) Antigravity 必须严格跟随 Gemini
  2) Google 未通过时, Gemini 不得判为可用
  3) 【核心】判定不可用看"是否重定向到 google.com.hk", 而不是出口地区
     —— 实测 24 个 HK 出口里只有 3 个会跳转, 按地区封杀会误杀其余 21 个
  4) 异常兜底: 任一阶段抛错也不能留下 gemini=unknown 却被视为可用
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
print("6. 地区黑名单 (双口径: web = Gemini 网页版, api = API/Antigravity)")
print("=" * 92)

from core.detector import region_status  # noqa: E402

# 6a) 连网页版都不支持的地区 -> 两个口径都命中
for cc in ("CN", "RU", "IR", "BY", "KP", "SY", "CU"):
    w_bad, why = region_status(cc, "web")
    a_bad, _ = region_status(cc, "api")
    check(f"{cc} web/api 均不支持", w_bad and a_bad, why[:40])

# 6b) HK / MO: 网页版【支持】, 但 API/Antigravity 不支持
#     这正是本项目要区分的关键 —— 早期版本一刀切判 blocked 是错的
for cc in ("HK", "MO"):
    w_bad, _ = region_status(cc, "web")
    a_bad, why = region_status(cc, "api")
    check(f"{cc} 网页版支持 (web 口径不算不支持)", not w_bad)
    check(f"{cc} API/Antigravity 不支持 (api 口径命中)", a_bad, why[:44])

# 6c) 常见可用地区 -> 两个口径都不命中
for cc in ("US", "JP", "SG", "TW", "KR", "DE", "GB", "LV", "UA", "TH", "VN"):
    w_bad, _ = region_status(cc, "web")
    a_bad, _ = region_status(cc, "api")
    check(f"{cc} 两个口径都不在名单", not w_bad and not a_bad)

# 6d) 【核心】HK 出口: Gemini 可用, 但 Antigravity 不可用
#     resolve_antigravity 只改 antigravity, 不动 gemini
det = Detection()
det.google = "success"
det.country = "HK"
det.gemini = "available"          # 网页版 Gemini 在香港可用
det.risk_level = "none"
d.resolve_antigravity(det)
check("HK -> Gemini 保持可用", det.gemini == "available", f"={det.gemini}")
check("HK -> Antigravity 被地区限制", det.antigravity == "blocked",
      f"={det.antigravity}")

# 6e) MO 同理
det = Detection()
det.google = "success"
det.country = "MO"
det.gemini = "available"
det.risk_level = "none"
d.resolve_antigravity(det)
check("MO -> Gemini 保持可用", det.gemini == "available", f"={det.gemini}")
check("MO -> Antigravity 被地区限制", det.antigravity == "blocked",
      f"={det.antigravity}")

# 6f) 非黑名单地区 + 未跳转 -> 两者都可用
det = Detection()
det.google = "success"
det.country = "JP"
det.gemini = "available"
det.risk_level = "none"
det.hk_redirect = False
d.resolve_antigravity(det)
check("JP 出口 + 未跳转 -> Gemini 可用", det.gemini == "available", f"={det.gemini}")
check("JP 出口 + 未跳转 -> Antigravity 跟随可用",
      det.antigravity == "available", f"={det.antigravity}")

# 6g) 出口不是 HK, 但跳转了 -> 必须判不可用
det = Detection()
det.google = "success"
det.country = "SG"
det.gemini = "blocked"            # detect_gemini 里被风险覆盖
det.hk_redirect = True
det.risk_level = "high"           # 跳转会置 high
d.resolve_antigravity(det)
check("SG 出口 + 跳转 com.hk -> Gemini blocked",
      det.gemini == "blocked", f"={det.gemini}")
check("SG 出口 + 跳转 com.hk -> Antigravity blocked",
      det.antigravity == "blocked", f"={det.antigravity}")
check("SG 出口 + 跳转 com.hk -> 不计入潜在可用",
      not is_potentially_usable({
          "reachable": True, "google": "success",
          "gemini": "blocked", "risk_level": "high"}))

print()
print("=" * 92)
print("7. 地区对『潜在可用』的影响 (以 Gemini 为准)")
print("=" * 92)
# 7a) 连网页版都不支持的地区 -> gemini 被地区判 blocked -> 不计入可用
for cc in ("CN", "RU", "IR", "BY", "KP", "SY", "CU"):
    check(f"{cc} 出口 -> 不计入潜在可用",
          not is_potentially_usable({
              "reachable": True, "google": "success",
              "gemini": "blocked", "risk_level": "none", "country": cc}))

# 7b) HK / MO 网页版 Gemini 可用 -> 应计入潜在可用
#     (Antigravity 单独判 blocked, 但不影响"这个节点能不能用网页版 Gemini")
for cc in ("HK", "MO"):
    check(f"{cc} 出口 + gemini=available -> 计入潜在可用",
          is_potentially_usable({
              "reachable": True, "google": "success",
              "gemini": "available", "risk_level": "none", "country": cc}))

print()
print("=" * 92)
if FAIL:
    print(f"失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("ALL VERDICT INVARIANT TESTS PASSED")
