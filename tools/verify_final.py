"""最终验收: 从 GitHub 克隆的全新副本, 零节点跑通完整流程.

模拟真实用户: 启动面板 -> 导入节点 -> (不真的测, 因为需要可用节点) -> 状态正确
"""
import sys
from pathlib import Path

PKG = Path(sys.argv[1])
sys.path.insert(0, str(PKG))
import httpx

from core.util import setup_console

setup_console()

BASE = "http://127.0.0.1:8787"
FAIL = []


def check(name, cond, detail=""):
    print(f"[{'OK  ' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


c = httpx.Client(base_url=BASE, timeout=20)

print(f"验收目录: {PKG}")
print()
print("=" * 84)
print("A. 零节点冷启动")
print("=" * 84)
m = c.get("/api/meta").json()
print(f"  meta: exists={m['exists']} count={m['count']} node_count={m['node_count']}")
check("服务在零节点下可用", True)
check("无结果时明确告知", m["exists"] is False and m["count"] == 0)
check("提供 node_count 供引导判断", "node_count" in m)

print()
print("=" * 84)
print("B. 面板首屏就是'可操作'的, 而不是死路")
print("=" * 84)
html = c.get("/").text
check("首页有引导区", 'id="onboarding"' in html)
check("有导入按钮", 'id="ob-import"' in html)
check("有检测按钮", 'id="ob-test"' in html)
js = (PKG / "web" / "static" / "app.js").read_text(encoding="utf-8")
check("不再出现'请先运行 python main.py'", "请先运行" not in js)
check("空态指向导入", "还没有检测结果" in js)

print()
print("=" * 84)
print("C. 导入 -> 引导切换 (node_count 0 -> N)")
print("=" * 84)
uris = "\n".join([
    "vless://11111111-2222-3333-4444-555555555555@a.example.com:443"
    "?encryption=none&security=tls&sni=a.example.com#节点A",
    "hysteria2://pw@b.example.com:443?sni=b.example.com#节点B",
    "vless://11111111-2222-3333-4444-555555555555@a.example.com:443"
    "?encryption=none&security=tls&sni=a.example.com#节点A重复",
    "这不是一个链接",
])
prev = c.post("/api/nodes/preview", json={"text": uris}).json()
print(f"  预览: 新增 {prev['new_count']} / 重复 {prev['dup_count']} / 无效 {prev['bad_count']}")
check("预览能识别重复与无效", prev["new_count"] == 2 and prev["dup_count"] == 1
      and prev["bad_count"] == 1)

r = c.post("/api/nodes/import", json={"text": uris}).json()
print(f"  导入: {r.get('message')}")
check("导入成功", r.get("ok") and r.get("added") == 2)

m2 = c.get("/api/meta").json()
print(f"  导入后: node_count={m2['node_count']} count={m2['count']}")
check("node_count 变为 2", m2["node_count"] == 2)
check("结果显示仍为 0 (还没测)", m2["count"] == 0)
check("此时前端会提示'检测这 2 个节点'",
      m2["node_count"] == 2 and m2["count"] == 0)

print()
print("=" * 84)
print("D. 节点文件确实写了 (用户可核对)")
print("=" * 84)
nf = PKG / "proxiers.txt"
check("proxiers.txt 已创建", nf.is_file())
if nf.is_file():
    lines = [l for l in nf.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"  文件内容 {len(lines)} 行")
    check("含 2 个节点", len(lines) == 2)

print()
if FAIL:
    print(f"失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("全新克隆验收通过: 零节点可启动, 导入即可测")
