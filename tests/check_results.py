"""检查 results.json 质量: 延迟合理性、地区一致性、字段完整性."""
import io
import json
import sys

sys.path.insert(0, ".")
from core.util import setup_console

setup_console()

d = json.load(open("results.json", encoding="utf-8"))
print(f"总条目: {len(d)}")
print(f"ok=True: {sum(1 for r in d if r['ok'])}")
print(f"有出口IP: {sum(1 for r in d if r['ip'])}")
print(f"有地区: {sum(1 for r in d if r['country'])}")
print()

print("--- 可疑延迟 (可能未真正走通) ---")
susp = [r for r in d if r["latency"] and r["latency"] < 100]
for r in susp:
    print(f"  {r['latency']:>5}ms ok={r['ok']} cc={r['country']!r} "
          f"err={(r['error'] or '-')[:50]} tag={r['tag'][:38]}")
print(f"  小计 {len(susp)} 条")
print()

print("--- 最快 8 个 ok=True 节点 ---")
for r in [x for x in d if x["ok"]][:8]:
    print(f"  {r['country']:3} {r['protocol']:9} {r['latency']:>6}ms ip={r['ip']:16} "
          f"{r['tag'][:32]}")
print()

print("--- ok=True 但延迟异常低 (<200ms) 的条目 ---")
low = [r for r in d if r["ok"] and r["latency"] < 200]
for r in low:
    print(f"  {r['latency']:>5}ms {r['country']:3} ip={r['ip']:16} {r['tag'][:34]}")
print(f"  小计 {len(low)} 条")
print()

print("--- ok=True 但 antigravity 非 available 的条目 ---")
for r in d:
    if r["ok"] and r["antigravity"] != "available":
        print(f"  cc={r['country']:3} gem={r['gemini']:11} ag={r['antigravity']:11} "
              f"reason={r['antigravity_reason'][:66]}")
        print(f"      tag={r['tag'][:48]}")

print("--- google 非 success 但链路可达的条目 ---")
for r in d:
    if r["reachable"] and r["google"] != "success":
        print(f"  cc={r['country']:3} google={r['google']} status={r['google_status']} "
              f"tag={r['tag'][:34]}")

print("--- 地区 vs Gemini 判定 一致性抽样 ---")
from collections import Counter
c = Counter((r["country"], r["gemini"]) for r in d if r["country"])
for (cc, gem), n in sorted(c.items()):
    print(f"  {cc:3} {gem:12} x{n}")
