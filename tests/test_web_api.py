"""Web API 端到端测试: 直接调用真实服务, 校验每个端点.

前置: web/server.py 已在 127.0.0.1:8787 运行.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx

from core.util import setup_console

setup_console()

BASE = "http://127.0.0.1:8787"
FAIL = []


def check(name, cond, detail=""):
    mark = "OK  " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


c = httpx.Client(base_url=BASE, timeout=20)

print("=" * 90)
print("1. 页面与静态资源")
print("=" * 90)
r = c.get("/")
check("GET /  返回 HTML", r.status_code == 200 and "<!DOCTYPE html>" in r.text,
      f"status={r.status_code} len={len(r.text)}")
check("页面含标题", "代理节点体检面板" in r.text)

for f, ct in (("style.css", "css"), ("app.js", "javascript")):
    rr = c.get(f"/static/{f}")
    check(f"GET /static/{f}", rr.status_code == 200 and len(rr.text) > 100,
          f"status={rr.status_code} len={len(rr.text)}")

print()
print("=" * 90)
print("2. 数据接口")
print("=" * 90)
meta = c.get("/api/meta").json()
check("/api/meta 可读", meta["exists"] and meta["count"] > 0, f"count={meta['count']}")
check("/api/meta 有时间戳", bool(meta["updated_at"]), meta["updated_at"])

s = c.get("/api/summary").json()
check("/api/summary 字段齐全",
      all(k in s for k in ("total", "usable", "gemini_available", "countries")))
check("usable <= total", s["usable"] <= s["total"], f"{s['usable']}/{s['total']}")

f = c.get("/api/facets").json()
check("/api/facets 三组", all(k in f for k in ("countries", "protocols", "gemini")))
# 地区计数只统计拿到出口地区的节点; 未取到 IP 的节点(如已失效节点)不计入,
# 因此这里应与 summary.countries 一致, 而不是与 total 一致.
check("facets 地区计数 == summary.countries",
      sum(i["count"] for i in f["countries"]) == sum(s["countries"].values()),
      f"{sum(i['count'] for i in f['countries'])} vs {sum(s['countries'].values())}")
check("facets 地区计数 <= total",
      sum(i["count"] for i in f["countries"]) <= s["total"])

print()
print("=" * 90)
print("3. /api/nodes 查询")
print("=" * 90)
alln = c.get("/api/nodes").json()
check("默认返回全部", alln["total"] == s["total"], f"total={alln['total']}")

jp = c.get("/api/nodes", params={"country": "JP"}).json()
check("按地区筛选", jp["total"] == s["countries"].get("JP", 0), f"JP={jp['total']}")

uo = c.get("/api/nodes", params={"usable_only": True}).json()
check("usable_only", uo["total"] == s["usable"], f"{uo['total']} vs {s['usable']}")
# 可用性的判定规则: 分类结果以分类结论为准, 非分类结果走 Google 规则.
# 两种情况都必须"链路可达".
def _usable_ok(i):
    if not i.get("reachable"):
        return False
    cats = i.get("categories") or {}
    if cats:
        return all(str((v or {}).get("verdict", "")) == "available"
                   for v in cats.values())
    return (i.get("google") == "success"
            and i.get("gemini") in ("available", "likely")
            and str(i.get("risk_level", "none")) != "high")

check("usable_only 项都满足条件", all(_usable_ok(i) for i in uo["items"]))

lat = c.get("/api/nodes", params={"sort": "latency"}).json()["items"]
lats = [i["latency"] for i in lat if i.get("reachable") and i.get("latency")]
check("延迟排序升序", lats == sorted(lats), f"前5={lats[:5]}")
check("延迟排序首项可达", lat[0]["reachable"], f"lat={lat[0]['latency']}ms")

srch = c.get("/api/nodes", params={"search": "日本"}).json()
check("中文搜索可用", srch["total"] > 0, f"命中 {srch['total']}")

print()
print("=" * 90)
print("4. 导出接口 (核心需求)")
print("=" * 90)
r = c.get("/api/export", params={"scope": "usable"})
check("导出 usable 状态码 200", r.status_code == 200)
lines = [x for x in r.text.strip().split("\n") if x.strip()]
check("导出行数 == 潜在可用数", len(lines) == s["usable"], f"{len(lines)} vs {s['usable']}")
check("每行都是合法 URI",
      all(x.startswith(("vless://", "vmess://", "trojan://", "ss://",
                        "hysteria2://", "hy2://", "tuic://")) for x in lines))
check("无重复行", len(set(lines)) == len(lines))
check("以换行结尾", r.text.endswith("\n"))
check("Content-Type 为纯文本", "text/plain" in r.headers.get("content-type", ""))

# 交叉验证: 导出的 URI 必须全部来自"潜在可用"节点
usable_uris = {i["node"] for i in uo["items"]}
check("导出的 URI 全部来自潜在可用集合",
      set(lines) <= usable_uris, f"越界 {len(set(lines) - usable_uris)} 条")

# 反向: 每个潜在可用节点的 URI 都应被导出
check("潜在可用节点无遗漏", usable_uris <= set(lines),
      f"遗漏 {len(usable_uris - set(lines))} 条")

# 严格校验: 被限制的节点不得出现
blocked_uris = {i["node"] for i in c.get("/api/nodes", params={"gemini": "blocked"}).json()["items"]}
check("被限制节点未混入导出", not (set(lines) & blocked_uris),
      f"混入 {len(set(lines) & blocked_uris)} 条")

# 下载模式
rd = c.get("/api/export", params={"scope": "usable", "download": "true"})
check("download=true 带附件头",
      "attachment" in rd.headers.get("content-disposition", ""),
      rd.headers.get("content-disposition", "")[:70])

# 勾选导出
ids = ",".join(i["node_id"] for i in uo["items"][:5])
rs = c.get("/api/export", params={"scope": "all", "ids": ids})
sel = [x for x in rs.text.strip().split("\n") if x.strip()]
check("按 ids 导出", len(sel) == 5, f"得到 {len(sel)} 条")

# scope=all
ra = c.get("/api/export", params={"scope": "all"})
check("scope=all 全部导出",
      len([x for x in ra.text.strip().split("\n") if x.strip()]) == s["total"])

# 参数校验
rb = c.get("/api/export", params={"scope": "bogus"})
check("非法 scope 被拒绝 (422)", rb.status_code == 422, f"status={rb.status_code}")

print()
print("=" * 90)
print("5. 重载接口")
print("=" * 90)
rr = c.post("/api/reload").json()
check("/api/reload 成功", rr.get("ok") and rr["count"] == s["total"], f"count={rr['count']}")

print()
print("=" * 90)
if FAIL:
    print(f"失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("ALL WEB API TESTS PASSED")
