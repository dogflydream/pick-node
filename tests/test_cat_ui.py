"""检查前端分类 UI 是否就绪 (静态资源 + 关键元素)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import httpx

from core.util import setup_console

setup_console()

BASE = "http://127.0.0.1:8787"
FAIL = []


def check(name, cond, detail=""):
    print(f"[{'OK  ' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


c = httpx.Client(base_url=BASE, timeout=15)
html = c.get("/").text
js = c.get("/static/app.js").text
css = c.get("/static/style.css").text

print("=" * 84)
print("1. 分类切换 UI 元素")
print("=" * 84)
check("有分类导航容器", 'id="cats"' in html)
check("有分类列表容器", 'id="cats-list"' in html)
check("有分类提示", 'id="cats-hint"' in html)
check("有服务筛选下拉", 'id="f-svc"' in html)

print()
print("=" * 84)
print("2. 前端逻辑")
print("=" * 84)
for fn in ("loadCategories", "selectCategory", "verdictCell", "servicesCell",
           "updateTableHead", "withCat"):
    check(f"定义了 {fn}()", f"function {fn}" in js)

check("请求会带 category 参数", "p.set('category', state.cat)" in js)
check("重新检测会传分类", "body.categories = [state.cat]" in js)
check("init 先加载分类", "await loadCategories()" in js)

print()
print("=" * 84)
print("3. 样式")
print("=" * 84)
for cls in (".cats", ".cat__name", ".verdict--available", ".verdict--blocked",
            ".svc--ok", ".svc--bad"):
    check(f"样式含 {cls}", cls in css)

print()
print("=" * 84)
print("4. 表头由前端动态生成 (不再写死 Google 列)")
print("=" * 84)
check("HTML 里不再有写死的 Google 表头", "<th scope=\"col\">Google</th>" not in html)
check("HTML 里不再有写死的 Gemini 表头", "<th scope=\"col\">Gemini</th>" not in html)
check("JS 里动态生成 Google 列", "['Google', '风险', 'Gemini', 'Antigravity']" in js)
check("JS 里动态生成分类列", "mk('结论'" in js or "mk('明细'" in js)

print()
if FAIL:
    print(f"失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("UI 检查通过")
