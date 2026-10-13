"""全新克隆验收: 分类功能 (XAI / GOOGLE) 与 CF 判别."""
import sys
from pathlib import Path

PKG = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
sys.path.insert(0, str(PKG))

from core.cfclass import CF_BLOCKED, CF_CHALLENGE, classify_response
from core.categories import all_categories, build
from core.util import setup_console

setup_console()

FAIL = []


def check(name, cond, detail=""):
    print(f"[{'OK  ' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


print(f"验收目录: {PKG}")
print()
print("=" * 84)
print("1. 分类注册")
print("=" * 84)
cats = all_categories()
print(f"  已注册: {cats}")
check("含 XAI", "xai" in cats)
check("含 GOOGLE", "google" in cats)

x = build("xai", 10)
g = build("google", 10)
check("XAI 只测 grok", [s.key for s in x.services] == ["grok"],
      str([s.key for s in x.services]))
check("GOOGLE 测 google/gemini/antigravity",
      [s.key for s in g.services] == ["google", "gemini", "antigravity"],
      str([s.key for s in g.services]))

print()
print("=" * 84)
print("2. CF 判别 —— 需求核心: 403 不能直接判死")
print("=" * 84)
sample = PKG / "tests" / "fixtures" / "cf_block_grok.html"
check("脱敏样本存在", sample.is_file())
html = sample.read_text(encoding="utf-8", errors="replace")
c = classify_response(403, html, {"server": "cloudflare"}, "https://grok.com/")
print(f"  封禁样本 -> {c.kind}  ray={c.ray_id}  host={c.blocked_host}")
check("封禁页判为 cf_blocked", c.kind == CF_BLOCKED)
check("判为不可用", c.available is False)
check("提取被封站点", c.blocked_host == "grok.com", c.blocked_host)

challenge = ('<html><head><title>Just a moment...</title></head><body>'
             '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/'
             'challenge.js"></script></body></html>')
cc = classify_response(403, challenge, {"server": "cloudflare"})
print(f"  盾样本   -> {cc.kind}")
check("盾判为 cf_challenge", cc.kind == CF_CHALLENGE)
check("盾判为可用 (浏览器能过)", cc.available is True)

print()
print("=" * 84)
print("3. XAI 判定链路")
print("=" * 84)
judge = x.services[0].judge

v, r = judge(403, html, {"server": "cloudflare"}, "https://grok.com/")
check("403+封禁页 -> blocked", v == "blocked", v)
v, r = judge(403, challenge, {"server": "cloudflare"}, "https://grok.com/")
check("403+盾 -> available", v == "available", v)
v, r = judge(200, "<html>grok</html>", {}, "https://grok.com/")
check("200 -> available", v == "available", v)
v, r = judge(0, "", {}, "")
check("请求失败 -> unavailable", v == "unavailable", v)

print()
print("=" * 84)
print("4. 仅测试自己分类的项 (后端不做其它事)")
print("=" * 84)
check("XAI 服务列表不含 google 服务",
      not ({"google", "gemini", "antigravity"} & {s.key for s in x.services}))
check("GOOGLE 服务列表不含 grok", "grok" not in {s.key for s in g.services})

print()
if FAIL:
    print(f"失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("全新克隆验收通过: 分类 + CF 判别均可用")
