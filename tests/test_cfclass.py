"""CF 分类单元测试.

关键验证: 用仓库里真实的 test_grok.html (grok.com 被 CF 封禁的实测样本)
        必须判为 cf_blocked, 而不是 cf_challenge.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.cfclass import (CF_BLOCKED, CF_CHALLENGE, CF_ERROR_5XX,
                          CF_RATE_LIMITED, SITE_DENIED, SITE_OK,
                          UNKNOWN_DENIED, classify_response)
from core.util import setup_console

setup_console()

FAIL = []


def check(name, cond, detail=""):
    print(f"[{'OK  ' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


print("=" * 92)
print("1. 真实样本: grok.com 被 CF 封禁的响应 (已脱敏)")
print("=" * 92)
sample = ROOT / "tests" / "fixtures" / "cf_block_grok.html"
check("样本文件存在", sample.is_file(), str(sample))
html = sample.read_text(encoding="utf-8", errors="replace")

c = classify_response(403, html, {"server": "cloudflare"}, "https://grok.com/")
print(f"  kind={c.kind}  available={c.available}")
print(f"  reason={c.reason}")
print(f"  ray_id={c.ray_id}  blocked_host={c.blocked_host}")
check("403 + CF 封禁页 -> cf_blocked", c.kind == CF_BLOCKED, c.kind)
check("判为不可用", c.available is False)
check("识别出是 Cloudflare", c.is_cloudflare)
check("提取到 Ray ID (脱敏后为固定值)", bool(c.ray_id), c.ray_id)
check("提取到被封站点", c.blocked_host == "grok.com", c.blocked_host)

print()
print("=" * 92)
print("2. CF 盾 (人机验证) 必须与封禁区分开 —— 这是需求核心")
print("=" * 92)
challenge_cases = [
    ("js 挑战", '<html><head><title>Just a moment...</title></head>'
                '<body><script src="/cdn-cgi/challenge-platform/h/b/orchestrate/'
                'challenge.js"></script></body></html>'),
    ("Turnstile", '<html><body><div class="cf-turnstile" '
                  'data-sitekey="x"></div><script src='
                  '"https://challenges.cloudflare.com/turnstile/v0/api.js">'
                  '</script></body></html>'),
    ("verify human", '<html><head><title>Attention Required! | Cloudflare'
                     '</title></head><body>Verify you are human'
                     '</body></html>'),
    ("checking browser", '<html><body>Checking your browser before accessing '
                         'example.com</body></html>'),
]
for label, body in challenge_cases:
    c = classify_response(403, body, {"server": "cloudflare"})
    ok = c.kind == CF_CHALLENGE
    check(f"{label} -> cf_challenge (可用)", ok, c.kind)
    check(f"  {label} 判为可用", c.available is True)

print()
print("=" * 92)
print("3. 真实 CF 封禁页 vs 盾: 关键区别是封禁页有 block_headline")
print("=" * 92)
# 封禁页里也常引用 challenge 脚本, 但不能因此误判为盾
mixed = ('<html><head><title>Attention Required! | Cloudflare</title></head>'
         '<body><div id="cf-error-details">'
         '<h1 data-translate="block_headline">Sorry, you have been blocked</h1>'
         '<h2><span data-translate="unable_to_access">You are unable to access'
         '</span> grok.com</h2></div>'
         '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
         '</body></html>')
c = classify_response(403, mixed, {"server": "cloudflare"})
check("同时含 challenge 脚本时仍判 cf_blocked", c.kind == CF_BLOCKED, c.kind)
check("  判为不可用", c.available is False)

print()
print("=" * 92)
print("4. 正常 200 页面")
print("=" * 92)
c = classify_response(200, '<html><body>Welcome to grok</body></html>',
                      {"server": "cloudflare"})
check("200 -> site_ok", c.kind == SITE_OK, c.kind)
check("判为可用", c.available is True)

print()
print("=" * 92)
print("5. 其它状态码")
print("=" * 92)
cases = [
    (429, "", {"server": "cloudflare"}, CF_RATE_LIMITED, False),
    (503, "", {"server": "cloudflare"}, CF_ERROR_5XX, False),
    (401, "<html>Please log in to continue</html>", {}, SITE_DENIED, False),
    (418, "<html>teapot</html>", {}, UNKNOWN_DENIED, False),
    (500, "", {}, UNKNOWN_DENIED, False),
]
for status, body, hdrs, expect_kind, expect_avail in cases:
    c = classify_response(status, body, hdrs)
    check(f"HTTP {status} -> {expect_kind}", c.kind == expect_kind,
          f"得到 {c.kind}")
    check(f"  HTTP {status} available={expect_avail}",
          c.available is expect_avail)

print()
print("=" * 92)
print("6. 边界: 空 body / 无响应头")
print("=" * 92)
c = classify_response(403, "", {})
check("403 空 body -> unknown_denied (不误判为盾)", c.kind == UNKNOWN_DENIED,
      c.kind)
check("  判为不可用", c.available is False)
c = classify_response(200, "", {})
check("200 空 body -> site_ok", c.kind == SITE_OK, c.kind)

print()
print("=" * 92)
if FAIL:
    print(f"失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("ALL CF CLASSIFICATION TESTS PASSED")
