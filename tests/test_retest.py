"""网页端「重新检测」功能测试.

覆盖:
  1. 端点存在性与参数校验
  2. 真跑一个小批次 (2 个节点), 观察状态流转
  3. merge 写回: 只重测 2 个节点, 其余 130 条的旧结果必须保留
  4. 并发冲突: 运行中再次启动应被拒绝
"""
import json
import sys
import time
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


c = httpx.Client(base_url=BASE, timeout=45)

print("=" * 92)
print("1. 端点存在性")
print("=" * 92)
meta = c.get("/api/meta").json()
check("/api/meta 带 concurrency", "concurrency" in meta, f"={meta.get('concurrency')}")
check("/api/meta 带 nodes_file", meta.get("nodes_file") == "proxiers.txt")

st = c.get("/api/retest/status").json()
check("/api/retest/status 可用", "status" in st, f"status={st['status']}")
check("初始状态为 idle", st["status"] in ("idle", "done", "cancelled"), st["status"])

# 基线: 必须在【启动重测之前】从当前 results.json 取样.
# 不能用历史的 results_backup.json —— 只要之前跑过任何一次重测,
# 被重测过的节点就会与备份不同, 导致误报.
base_results = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))
base_ids = {r["node_id"] for r in base_results}
print(f"   基线(重测前快照): {len(base_results)} 条")

print()
print("=" * 92)
print("2. 启动一个小批次重测 (2 个节点)")
print("=" * 92)
pick_ids = [r["node_id"] for r in base_results if r.get("reachable")][:2]
print(f"   选中 node_id: {pick_ids}")

r = c.post("/api/retest", json={"node_ids": pick_ids, "concurrency": 2})
check("POST /api/retest 返回 200", r.status_code == 200, f"status={r.status_code}")
check("返回 ok", r.json().get("ok") is True)

# 立刻再启动一次, 应被拒
r2 = c.post("/api/retest", json={"node_ids": pick_ids})
check("运行中重复启动被拒绝 (409)", r2.status_code == 409,
      f"status={r2.status_code} {r2.text[:60]}")

print()
print("=" * 92)
print("3. 观察状态流转")
print("=" * 92)
t0 = time.time()
last = None
saw_running = False
while time.time() - t0 < 180:
    d = c.get("/api/retest/status").json()
    sig = (d["status"], d["done"], d["total"])
    if sig != last:
        print(f"   t={time.time()-t0:5.1f}s  status={d['status']:9} "
              f"{d['done']}/{d['total']}  results={d.get('result_count', 0)}")
        last = sig
        # 运行期间 results.json 必须始终是合法 JSON (写回不是"先清空再写")
        try:
            _mid = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))
            check(f"运行中 results.json 仍可解析 ({len(_mid)} 条)", True)
        except Exception as e:
            check("运行中 results.json 仍可解析", False, f"{type(e).__name__}: {e}")
    if d["status"] == "running":
        saw_running = True
    if d["status"] in ("done", "cancelled", "failed"):
        final = d
        break
    time.sleep(0.8)
else:
    final = c.get("/api/retest/status").json()

check("观察到 running 状态", saw_running)
check("最终状态为 done", final["status"] == "done",
      f"status={final['status']} err={final.get('error','')}")
check("跑完 2 个节点", final["done"] == 2, f"done={final['done']}")

print()
print("=" * 92)
print("4. merge 写回验证 (关键)")
print("=" * 92)
after = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))
after_ids = {r["node_id"] for r in after}
print(f"   重测后: {len(after)} 条")

check("总条数不变 (没有丢节点)", len(after) == len(base_results),
      f"{len(after)} vs {len(base_results)}")
check("所有原 node_id 仍在", base_ids <= after_ids,
      f"缺失 {len(base_ids - after_ids)} 条")

# 未重测的节点应保持原值
untouched = [i for i in base_ids if i not in set(pick_ids)]
old_map = {r["node_id"]: r for r in base_results}
new_map = {r["node_id"]: r for r in after}
same = sum(1 for i in untouched if old_map[i].get("latency") == new_map[i].get("latency")
           and old_map[i].get("ip") == new_map[i].get("ip"))
check("未重测节点结果保持不变", same == len(untouched),
      f"{same}/{len(untouched)} 完全一致")

# 重测的两个节点应被更新过 (时间/延迟可能变, 至少字段齐全)
for i in pick_ids:
    row = new_map.get(i)
    check(f"重测节点 {i[:8]} 结果完整",
          row is not None and "risk_level" in row and "gemini" in row)

print()
print("=" * 92)
print("5. 代理池同步")
print("=" * 92)
pool = (ROOT / "proxiers_google.txt").read_text(encoding="utf-8")
lines = [x for x in pool.strip().split("\n") if x.strip()]
# 池内容应与"所有结果文件里判定可用"的节点一致 (并集).
# 为什么是并集: 未指定分类的重测会把池按各结果文件重建, 避免用局部结果
# 覆盖掉全量池 (曾经踩过: 池被覆盖成空).
from core.store import ResultStore, is_potentially_usable  # noqa: E402

_expect_set: set[str] = set()
for _p in [ROOT / "results.json", ROOT / "results_google.json",
           ROOT / "results_xai.json"]:
    if not _p.is_file():
        continue
    for _r in ResultStore(_p).all():
        _u = str(_r.get("node", "")).strip()
        if _u and is_potentially_usable(_r):
            _expect_set.add(_u)
check("代理池与可用集合一致 (各结果文件并集)",
      set(lines) == _expect_set,
      f"池 {len(lines)} 条 vs 可用 {len(_expect_set)} 条")
check("代理池均为合法 URI",
      all(x.startswith(("vless://", "vmess://", "trojan://", "ss://",
                        "hysteria2://", "hy2://", "tuic://")) for x in lines))

print()
print("=" * 92)
if FAIL:
    print(f"失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("ALL RETEST TESTS PASSED")
