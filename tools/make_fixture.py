"""把真实的 test_grok.html 脱敏成可公开的测试样本.

保留所有 CF 判定所需的特征 (cf-error-details / block_headline / 标题结构),
只替换掉出口 IP 与 Ray ID 这类与本人相关的值。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.util import setup_console

setup_console()

src = ROOT / "test_grok.html"
dst = ROOT / "tests" / "fixtures" / "cf_block_grok.html"
dst.parent.mkdir(parents=True, exist_ok=True)

html = src.read_text(encoding="utf-8", errors="replace")
before_len = len(html)

# 1) 出口 IP -> 文档保留地址 (RFC 5737)
html = re.sub(r"\b(?!127\.0\.0\.1)(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b",
              "203.0.113.10", html)

# 2) Ray ID -> 固定假值 (保持格式, 判定用不到具体值)
html = re.sub(r"(Ray ID:?\s*(?:<[^>]*>\s*)*)([0-9a-f]{8,})",
              r"\g<1>0000000000000000", html, flags=re.I)
html = re.sub(r"__CF\$cv\$params=\{r:'[0-9a-f]+'",
              "__CF$cv$params={r:'0000000000000000'", html)

# 3) 顶部注释里的时间戳 (t:'...') 与 beacon token 不是隐私, 保留原样

dst.write_text(html, encoding="utf-8")

print(f"源文件  : {src}  ({before_len} 字节)")
print(f"脱敏样本: {dst}  ({len(html)} 字节)")
print()
print("校验: 判定所需的特征是否都还在")
for k in ("cf-error-details", "block_headline", "you have been blocked",
          "unable to access", "grok.com"):
    print(f"  {k!r:24} {'OK' if k in html.lower() else 'MISSING'}")
print()
print("校验: 敏感值是否已替换")
left = re.findall(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", html)
print(f"  剩余 IP: {sorted(set(left))}")
ray = re.findall(r"Ray ID:?\s*(?:<[^>]*>\s*)*([0-9a-f]{8,})", html, re.I)
print(f"  剩余 Ray: {sorted(set(ray))}")
