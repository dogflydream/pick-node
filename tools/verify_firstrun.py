"""验证全新克隆下 Web 面板的首屏引导逻辑 (无节点 / 无结果).

不依赖浏览器: 直接检查接口返回值 + 前端渲染逻辑的关键分支。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import httpx

from core.util import setup_console

setup_console()

BASE = "http://127.0.0.1:8792"
FAIL = []


def check(name, cond, detail=""):
    print(f"[{'OK  ' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


c = httpx.Client(base_url=BASE, timeout=20)

print("=" * 84)
print("1. /api/meta 是否提供引导所需信息")
print("=" * 84)
m = c.get("/api/meta").json()
print(f"  {json.dumps({k: m[k] for k in ('exists','count','node_count','nodes_file')}, ensure_ascii=False)}")
check("含 node_count 字段 (前端据此区分'没导入'与'没测过')",
      "node_count" in m)
check("全新克隆: exists=False", m["exists"] is False)
check("全新克隆: count=0", m["count"] == 0)
check("全新克隆: node_count=0", m["node_count"] == 0)

print()
print("=" * 84)
print("2. 无结果时各接口不应报错 (面板要能正常渲染)")
print("=" * 84)
for path in ("/api/summary", "/api/facets", "/api/nodes?usable_only=true",
             "/api/retest/status"):
    try:
        r = c.get(path)
        check(f"{path} -> {r.status_code}", r.status_code == 200)
    except Exception as e:
        check(f"{path} 可访问", False, f"{type(e).__name__}")

print()
print("=" * 84)
print("3. 首页包含引导区与导入入口")
print("=" * 84)
html = c.get("/").text
check("含 onboarding 区", 'id="onboarding"' in html)
check("含引导文案占位", 'id="ob-text"' in html)
check("含「导入节点」按钮", 'id="ob-import"' in html)
check("含「开始检测」按钮", 'id="ob-test"' in html)

js = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
check("前端不再引导用户跑命令行",
      "请先运行" not in js and "尚未生成" not in js)
check("前端有'还没有检测结果'引导", "还没有检测结果" in js)
check("空态文案指向导入操作", "导入节点" in js and "粘贴代理链接" in js)

print()
print("=" * 84)
print("4. 导入后状态应变化 (导入 1 个节点, 看 node_count)")
print("=" * 84)
uri = ("vless://11111111-2222-3333-4444-555555555555@example.com:443"
       "?encryption=none&security=tls&sni=example.com#smoke-test")
r = c.post("/api/nodes/import", json={"text": uri}).json()
print(f"  导入结果: {json.dumps(r, ensure_ascii=False)[:150]}")
check("导入成功", r.get("ok") and r.get("added") == 1)

m2 = c.get("/api/meta").json()
print(f"  导入后 node_count = {m2['node_count']}")
check("node_count 变为 1", m2["node_count"] == 1)
check("结果仍是 0 条 (还没测)", m2["count"] == 0)
check("此时应显示'检测这 N 个节点'引导",
      m2["node_count"] > 0 and m2["count"] == 0)

print()
if FAIL:
    print(f"失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("ALL ONBOARDING FLOW TESTS PASSED")
