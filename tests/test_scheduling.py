"""验证 Tester 滚动窗口重构 + 取消机制.

不依赖真实网络: 用假的 test_one 直接验证调度语义.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.parser import ParsedNode
from core.tester import Tester
from core.util import setup_console

setup_console()


class FakeTester(Tester):
    """把 test_one 换成可控的假实现, 只测调度与取消."""

    def __init__(self, *a, delay=0.15, **kw):
        super().__init__(*a, **kw)
        self.delay = delay
        self.started: list[str] = []
        self.lock = threading.Lock()

    def test_one(self, node):
        from core.tester import NodeResult
        with self.lock:
            self.started.append(node.tag)
        time.sleep(self.delay)
        return NodeResult(node=node.raw, tag=node.tag, node_id=node.node_id,
                          protocol=node.protocol.upper(), ok=True)


def mk(n=20):
    return [ParsedNode(raw=f"vless://x@{i}.example.com:443#n{i}",
                       protocol="vless", server=f"{i}.example.com", port=443,
                       uuid="11111111-2222-3333-4444-555555555555",
                       tag=f"n{i}", ok=True) for i in range(n)]


print("=" * 88)
print("TEST 1: 滚动窗口 — 并发不超过上限")
print("=" * 88)
t = FakeTester(launcher=None, detector=None, concurrency=3, delay=0.2)
peak = {"v": 0, "cur": 0}
orig = t.test_one


def wrapped(node):
    with t.lock:
        peak["cur"] += 1
        peak["v"] = max(peak["v"], peak["cur"])
    try:
        return orig(node)
    finally:
        with t.lock:
            peak["cur"] -= 1


t.test_one = wrapped
res = t.run(mk(12))
print(f"  节点数={len(res)}  观察到峰值并发={peak['v']} (上限 3)")
assert len(res) == 12, "结果数量不对"
assert peak["v"] <= 3, f"并发超过上限: {peak['v']}"
print("  [OK] 并发受控且全部完成")
print()

print("=" * 88)
print("TEST 2: 取消 — 剩余节点不再启动")
print("=" * 88)
flag = {"stop": False}
t2 = FakeTester(launcher=None, detector=None, concurrency=3, delay=0.4,
                should_stop=lambda: flag["stop"])
# 500ms 后请求取消
timer = threading.Timer(0.5, lambda: flag.__setitem__("stop", True))
timer.start()
t0 = time.time()
res2 = t2.run(mk(200))
el = time.time() - t0
timer.cancel()
print(f"  已启动 {len(t2.started)} 个 / 共 200 个节点, 耗时 {el:.2f}s, 返回 {len(res2)} 条")
assert len(t2.started) < 200, "取消后不应继续启动全部节点"
assert el < 3.0, f"取消后收尾太慢: {el:.2f}s"
print(f"  [OK] 取消生效: 仅启动 {len(t2.started)} 个, 未跑完 200 个")

print()
print("=" * 88)
print("TEST 3: 未取消时行为不变 (回归)")
print("=" * 88)
t3 = FakeTester(launcher=None, detector=None, concurrency=4, delay=0.05,
                should_stop=lambda: False)
res3 = t3.run(mk(16))
print(f"  节点数={len(res3)}  启动数={len(t3.started)}")
assert len(res3) == 16 and len(t3.started) == 16
print("  [OK] 正常路径无副作用")

print()
print("=" * 88)
print("TEST 4: 空列表与单节点边界")
print("=" * 88)
t4 = FakeTester(launcher=None, detector=None, concurrency=10, delay=0.01)
assert t4.run([]) == []
assert len(t4.run(mk(1))) == 1
print("  [OK] 边界正常")

print()
print("ALL TESTER SCHEDULING TESTS PASSED")
print()

# ---------------------------------------------------------------- 回归: 写回链路
print("=" * 88)
print("TEST 5: _merge_and_write 全链路 (含 summarize 类型正确性)")
print("=" * 88)
import json
import tempfile
from core.retest import RetestManager
from core.tester import NodeResult, summarize

tmp = Path(tempfile.mkdtemp())
rf, pf = tmp / "results.json", tmp / "pool.txt"
# 旧结果里放一条, 验证"未重测的保留"
rf.write_text(json.dumps([{
    "node": "vless://keep@1.1.1.1:443#keep", "node_id": "keep", "tag": "keep",
    "latency": 999, "ok": True, "reachable": True, "google": "success",
    "gemini": "available", "risk_level": "none",
}], ensure_ascii=False), encoding="utf-8")

m = RetestManager(root=tmp, nodes_file=tmp / "n.txt",
                  results_file=rf, pool_file=pf)
new = [
    NodeResult(node="vless://a@1.2.3.4:443#a", node_id="a", tag="a",
               latency=111, ok=True, reachable=True, google="success",
               gemini="available", risk_level="none"),
    NodeResult(node="vless://b@5.6.7.8:443#b", node_id="b", tag="b",
               latency=222, ok=True, reachable=True, google="success",
               gemini="blocked", risk_level="high"),
]
merged, added = m._merge_and_write(new)
print(f"  merged={len(merged)} added={added}")

# 关键: dict -> NodeResult 还原 + summarize 必须不抛异常
restored = [NodeResult(**r) for r in merged]
s = summarize(restored)
print(f"  summarize: total={s['total']} usable={s['usable']}")

assert len(merged) == 3, f"应保留 keep + 新增 a,b = 3, 实际 {len(merged)}"
assert added == 2
ids = {r["node_id"] for r in merged}
assert ids == {"keep", "a", "b"}, ids
assert s["total"] == 3
# pool 只应包含潜在可用 (a 可用, b 高风险被排除, keep 可用)
pool = pf.read_text(encoding="utf-8").strip().split("\n")
print(f"  pool={pool}")
assert any("a@" in p for p in pool), "a 应在池中"
assert not any("b@" in p for p in pool), "b 是高风险, 不应在池中"
print("  [OK] merge 保留旧条目 + 覆盖/新增 + summarize 类型正确 + 池过滤正确")
print()
print("ALL RETEST WRITEBACK TESTS PASSED")
