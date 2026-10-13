"""验证新增的 Antigravity 筛选项与双口径判定."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import httpx

from core.util import setup_console

setup_console()

BASE = "http://127.0.0.1:8787"
c = httpx.Client(base_url=BASE, timeout=20)
FAIL = []


def check(name, cond, detail=""):
    print(f"[{'OK  ' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


print("=" * 92)
print("1. /api/facets 是否含 antigravity")
print("=" * 92)
f = c.get("/api/facets").json()
check("含 antigravity 键", "antigravity" in f, f"keys={list(f.keys())}")
print(f"  antigravity: {f.get('antigravity')}")
print(f"  gemini     : {f.get('gemini')}")

print()
print("=" * 92)
print("2. 按服务筛选是否生效")
print("=" * 92)
ag_av = c.get("/api/nodes", params={"antigravity": "available"}).json()
ag_bl = c.get("/api/nodes", params={"antigravity": "blocked"}).json()
gem_av = c.get("/api/nodes", params={"gemini": "available"}).json()
print(f"  antigravity=available : {ag_av['total']}")
print(f"  antigravity=blocked   : {ag_bl['total']}")
print(f"  gemini=available      : {gem_av['total']}")
check("antigravity=available 有结果", ag_av["total"] > 0)
check("筛选结果确实都满足条件",
      all(i["antigravity"] == "available" for i in ag_av["items"]))
check("三项计数与 facets 一致",
      ag_av["total"] == next((x["count"] for x in f["antigravity"]
                              if x["value"] == "available"), -1),
      f"{ag_av['total']}")

print()
print("=" * 92)
print("3. 双口径: Gemini 可用数应多于 Antigravity (HK/MO 只算 Gemini)")
print("=" * 92)
check("gemini 可用数 > antigravity 可用数",
      gem_av["total"] > ag_av["total"],
      f"gemini={gem_av['total']} ag={ag_av['total']}")

# HK 节点: gemini available, antigravity blocked
hk = c.get("/api/nodes", params={"country": "HK"}).json()
hk_gem = [i for i in hk["items"] if i["gemini"] == "available"]
hk_ag = [i for i in hk["items"] if i["antigravity"] == "available"]
print(f"  HK 共 {hk['total']}: gemini 可用 {len(hk_gem)}, antigravity 可用 {len(hk_ag)}")
check("HK 的 Gemini 有可用节点", len(hk_gem) > 0)
check("HK 的 Antigravity 全部不可用", len(hk_ag) == 0)

print()
print("=" * 92)
print("4. 组合筛选")
print("=" * 92)
combo = c.get("/api/nodes", params={"country": "HK",
                                    "antigravity": "blocked"}).json()
print(f"  HK + antigravity=blocked : {combo['total']}")
check("组合筛选可用", combo["total"] > 0)

print()
if FAIL:
    print(f"失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("ALL ANTIGRAVITY FILTER TESTS PASSED")
