"""分类功能测试: API 层 + 逻辑层.

覆盖:
  - /api/categories 列出分类
  - 各分类数据源正确 (xai -> results_xai.json)
  - 分类结论筛选
  - CF 判定链路 (封禁 vs 盾)
  - 分类结果不互相污染
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import httpx

from core.cfclass import CF_BLOCKED, CF_CHALLENGE, classify_response
from core.util import setup_console

setup_console()

BASE = "http://127.0.0.1:8787"
FAIL = []


def check(name, cond, detail=""):
    print(f"[{'OK  ' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


c = httpx.Client(base_url=BASE, timeout=20)

print("=" * 92)
print("1. /api/categories")
print("=" * 92)
try:
    d = c.get("/api/categories").json()
    cats = d.get("categories", [])
    keys = [x["key"] for x in cats]
    print(f"  分类: {[(x['key'], x['label'], x['count']) for x in cats]}")
    check("返回分类列表", len(cats) >= 2)
    check("包含 xai 与 google", {"xai", "google"} <= set(keys), str(keys))
    check("每个分类带 file/count/exists",
          all({"file", "count", "exists"} <= set(x) for x in cats))
except Exception as e:
    check("/api/categories 可访问", False, f"{type(e).__name__}: {e}")

print()
print("=" * 92)
print("2. 各分类数据源独立 (不互相污染)")
print("=" * 92)
for key in ("xai", "google"):
    try:
        m = c.get("/api/meta", params={"category": key}).json()
        print(f"  {key:7}: file={m.get('file')} count={m.get('count')} exists={m.get('exists')}")
        check(f"{key} 的 file 与该分类匹配",
              m.get("file") == f"results_{key}.json", str(m.get("file")))
    except Exception as e:
        check(f"{key} meta 可访问", False, str(e))

# "全部" 是各分类的合并视图, 不应该等于某个具体分类的数据
try:
    mall = c.get("/api/meta", params={"category": "all"}).json()
    mg = c.get("/api/meta", params={"category": "google"}).json()
    mx = c.get("/api/meta", params={"category": "xai"}).json()
    print(f"  all    : count={mall.get('count')} (google={mg.get('count')}, xai={mx.get('count')})")
    check("全部视图节点数 >= 任一具体分类",
          mall.get("count", 0) >= max(mg.get("count", 0), mx.get("count", 0)),
          f"{mall.get('count')} vs {mg.get('count')}/{mx.get('count')}")
except Exception as e:
    check("all 视图", False, str(e))

print()
print("=" * 92)
print("3. 分类结果结构 (services / verdict / reason)")
print("=" * 92)
try:
    nodes = c.get("/api/nodes", params={"category": "xai"}).json()
    items = nodes.get("items", [])
    print(f"  xai 节点数: {nodes.get('total')}")
    if items:
        r0 = items[0]
        cats = r0.get("categories") or {}
        print(f"  首条分类键: {list(cats.keys())}")
        x = cats.get("xai", {})
        print(f"  xai.verdict   = {x.get('verdict')}")
        print(f"  xai.services  = {x.get('services')}")
        print(f"  xai.reason    = {str(x.get('reason'))[:70]}")
        check("categories 字段存在", bool(cats))
        check("xai 有 verdict", bool(x.get("verdict")))
        check("xai 有 services.grok", "grok" in (x.get("services") or {}))
        check("xai 有 reason", bool(x.get("reason")))
    else:
        print("  (无 xai 数据, 跳过结构检查)")
except Exception as e:
    check("xai 节点查询", False, str(e))

print()
print("=" * 92)
print("4. 分类结论筛选")
print("=" * 92)
try:
    allx = c.get("/api/nodes", params={"category": "xai"}).json()["total"]
    av = c.get("/api/nodes", params={"category": "xai",
                                     "cat_verdict": "available"}).json()["total"]
    un = c.get("/api/nodes", params={"category": "xai",
                                     "cat_verdict": "unavailable"}).json()["total"]
    print(f"  全部={allx}  可用={av}  不可用={un}")
    check("筛选结果不超过总数", av <= allx and un <= allx)
    check("可用+不可用 覆盖全部 (或更少)",
          av + un <= allx, f"{av}+{un} vs {allx}")
    # 校验筛出来的确实都满足
    one = c.get("/api/nodes", params={"category": "xai",
                                      "cat_verdict": "available"}).json()["items"]
    ok = all((i.get("categories", {}).get("xai", {}).get("verdict") == "available")
             for i in one)
    check("筛选出的条目结论一致", ok)
except Exception as e:
    check("分类筛选", False, str(e))

print()
print("=" * 92)
print("5. 导出带上分类")
print("=" * 92)
try:
    r = c.get("/api/export", params={"scope": "usable", "category": "xai"})
    print(f"  HTTP {r.status_code}, 行数 {len([x for x in r.text.strip().splitlines() if x.strip()])}")
    check("xai 导出可用", r.status_code == 200)
    if r.status_code == 200:
        lines = [x for x in r.text.strip().splitlines() if x.strip()]
        check("导出都是 URI",
              all(x.startswith(("vless://", "vmess://", "trojan://", "ss://",
                                "hysteria2://", "hy2://", "tuic://")) for x in lines))
except Exception as e:
    check("xai 导出", False, str(e))

print()
print("=" * 92)
print("6. CF 判定链路 (封禁 vs 盾) —— 本需求的核心")
print("=" * 92)
from core.categories import build

xai = build("xai", timeout=10)
judge = xai.services[0].judge

# 封禁页 -> blocked
blocked_html = ('<div id="cf-error-details">'
                '<h1 data-translate="block_headline">Sorry, you have been blocked</h1>'
                '</div>')
v, r = judge(403, blocked_html, {"server": "cloudflare"}, "https://grok.com/")
check("403 + CF 封禁页 -> blocked", v == "blocked", f"{v}: {r[:50]}")

# 盾 -> available
challenge_html = ('<html><head><title>Just a moment...</title></head><body>'
                  '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/'
                  'challenge.js"></script></body></html>')
v, r = judge(403, challenge_html, {"server": "cloudflare"}, "https://grok.com/")
check("403 + CF 盾 -> available", v == "available", f"{v}: {r[:50]}")

# 200 -> available
v, r = judge(200, "<html>grok app</html>", {}, "https://grok.com/")
check("200 -> available", v == "available", f"{v}")

# 网络失败 -> unavailable
v, r = judge(0, "", {}, "")
check("请求失败 -> unavailable", v == "unavailable", f"{v}")

print()
print("=" * 92)
print("7. 分类互不干扰 (测 xai 不应出现 google 字段)")
print("=" * 92)
try:
    items = c.get("/api/nodes", params={"category": "xai"}).json()["items"]
    if items:
        cats = items[0].get("categories") or {}
        check("xai 结果只含 xai 分类", "google" not in cats, str(list(cats)))
        svcs = (cats.get("xai") or {}).get("services") or {}
        check("xai 只测 grok", set(svcs.keys()) == {"grok"}, str(list(svcs)))
    else:
        print("  (无数据, 跳过)")
except Exception as e:
    check("互不干扰检查", False, str(e))

print()
if FAIL:
    print(f"失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("ALL CATEGORY TESTS PASSED")
