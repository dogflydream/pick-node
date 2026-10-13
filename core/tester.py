"""测试编排器: 把 节点 -> 实例 -> 检测 -> 结果 串成流水线.

并发模型:
  节点数量可能上百, 而每个实例都要占一个内核进程 (~50MB RSS) 和若干连接.
  因此用 线程池 + 信号量 做"有界并发": 最多 N 个节点同时在跑, 跑完一个
  立刻回收它的实例并让下一个进入, 保证内存/CPU 不会随节点数线性爆炸.

  线程而非进程: 每个节点的工作是"等网络 I/O", GIL 几乎不构成瓶颈,
  真正的并行发生在各个内核进程里.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .detector import Detector, country_name
from .launcher import Launcher
from .parser import ParsedNode


@dataclass
class NodeResult:
    """单个节点的最终结果 (也是 results.json 的条目结构)."""

    node: str = ""                 # 原始 URI
    tag: str = ""                  # 展示名
    node_id: str = ""
    protocol: str = ""
    server: str = ""
    port: int = 0
    local_port: int = 0
    kernel: str = ""
    ip: str = ""
    country: str = ""
    country_name: str = ""
    city: str = ""
    asn_org: str = ""
    country_source: str = ""
    google: str = "fail"           # success / fail
    google_status: int = 0
    google_final_url: str = ""
    google_redirected: bool = False
    hk_redirect: bool = False
    risk_flags: list[str] = field(default_factory=list)
    risk_level: str = "none"       # none / low / high
    risk_reason: str = ""
    gemini: str = "unknown"        # available / likely / blocked / unavailable / unknown
    gemini_reason: str = ""
    antigravity: str = "unknown"
    antigravity_reason: str = ""
    # ---- 延迟指标 (都含经过代理链路的开销) ----
    latency: int = 0               # google.com 端到端 (含 body, 旧口径)
    ttfb_ms: int = 0               # google.com 首字节 (不含 body 下载)
    connect_ms: int = 0            # 小请求的连接建立耗时 (TCP+TLS)
    rtt_min_ms: int = 0            # 小请求连测取最小值 —— 推荐用这个比快慢
    rtt_samples: list[int] = field(default_factory=list)
    reachable: bool = False
    ok: bool = False               # 综合可用性
    confidence: str = "low"
    error: str = ""
    errors: list[str] = field(default_factory=list)
    probes: list[dict[str, Any]] = field(default_factory=list)  # 各次探测明细

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Tester:
    """批量测试编排器."""

    def __init__(
        self,
        launcher: Launcher,
        detector: Detector,
        concurrency: int = 10,
        start_timeout: float = 12.0,
        on_event: Callable[[str, dict], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ):
        self.launcher = launcher
        self.detector = detector
        self.concurrency = max(1, concurrency)
        self.start_timeout = start_timeout
        self.on_event = on_event
        # 返回 True 时尽快停止派发新任务 (已提交的任务会跑完)
        self.should_stop = should_stop
        self._lock = threading.Lock()
        self._done = 0
        self._total = 0

    def _emit(self, event: str, payload: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event, payload)
            except Exception:
                pass  # 回调异常不能影响测试主流程

    # ------------------------------------------------------------ 单节点

    def test_one(self, node: ParsedNode) -> NodeResult:
        res = NodeResult(
            node=node.raw, tag=node.tag, node_id=node.node_id,
            protocol=node.protocol.upper(), server=node.server, port=node.port,
        )
        inst = None
        try:
            inst = self.launcher.start(node, wait_timeout=self.start_timeout)
            res.local_port = inst.port
            res.kernel = inst.kernel

            if not inst.start_ok:
                # 实例起不来: 可能是节点不可达、配置被拒或内核缺失
                res.error = inst.error
                res.ok = False
                return res

            det = self.detector.run(inst.proxy_url_socks)
            res.ip = det.ip
            res.country = det.country
            res.country_name = country_name(det.country)
            res.city = det.city
            res.asn_org = det.asn_org
            res.country_source = det.country_source
            res.google = det.google
            res.google_status = det.google_status
            res.google_final_url = det.google_final_url
            res.google_redirected = det.google_redirected
            res.hk_redirect = det.hk_redirect
            res.risk_flags = list(det.risk_flags)
            res.risk_level = det.risk_level
            res.risk_reason = det.risk_reason
            res.gemini = det.gemini
            res.gemini_reason = det.gemini_reason
            res.antigravity = det.antigravity
            res.antigravity_reason = det.antigravity_reason
            res.latency = det.latency
            res.ttfb_ms = det.ttfb_ms
            res.connect_ms = det.connect_ms
            res.rtt_min_ms = det.rtt_min_ms
            res.rtt_samples = list(det.rtt_samples)
            res.probes = list(det.probes)
            res.reachable = det.reachable
            res.confidence = det.confidence
            res.errors = list(det.errors)

            # 综合可用性: 链路通 + Google 可访问 + Gemini 不是明确被拒
            # Antigravity 跟随 Gemini, 故无需单独判断.
            res.ok = bool(
                det.reachable
                and det.google == "success"
                and det.gemini not in ("blocked", "unavailable")
            )
            return res
        except Exception as e:
            res.error = f"{type(e).__name__}: {e}"
            return res
        finally:
            # 无论成功失败, 立刻回收该节点的实例, 释放端口与内存
            if inst is not None:
                try:
                    self.launcher.stop(inst)
                except Exception:
                    pass
            with self._lock:
                self._done += 1
                self._emit("progress", {"done": self._done, "total": self._total,
                                        "tag": node.tag})

    # ------------------------------------------------------------ 批量

    def run(self, nodes: list[ParsedNode]) -> list[NodeResult]:
        """批量测试.

        采用"滚动窗口"提交: 始终只让 concurrency 个任务在跑, 完成一个才补一个.
        相比一次性提交全部, 这样有两个好处:
          1. 取消时能立刻停止后续节点, 不会把剩余任务全跑完
          2. 排队任务不占内存 (每个任务都要拉起一个内核进程)
        """
        self._total = len(nodes)
        self._done = 0
        results: list[NodeResult] = []
        if not nodes:
            return results

        workers = min(self.concurrency, len(nodes))
        it = iter(nodes)
        stopped = False

        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="tester") as ex:
            futures: dict = {}

            def submit_next() -> bool:
                """补充一个任务; 没有更多节点或已取消时返回 False."""
                if self.should_stop and self.should_stop():
                    return False
                try:
                    n = next(it)
                except StopIteration:
                    return False
                futures[ex.submit(self.test_one, n)] = n
                return True

            # 预填满窗口
            for _ in range(workers):
                if not submit_next():
                    break

            while futures:
                # 等任意一个完成
                done_futs = wait(list(futures.keys()),
                                 return_when=FIRST_COMPLETED).done
                for fut in done_futs:
                    node = futures.pop(fut)
                    try:
                        r = fut.result()
                    except Exception as e:
                        r = NodeResult(
                            node=node.raw, tag=node.tag, node_id=node.node_id,
                            protocol=node.protocol.upper(),
                            error=f"{type(e).__name__}: {e}",
                        )
                    results.append(r)
                    self._emit("result", r.to_dict())
                    # 每完成一个就补一个
                    if not submit_next():
                        if self.should_stop and self.should_stop() and not stopped:
                            stopped = True
                            self._emit("cancelled", {"done": len(results)})

        return results


# ---------------------------------------------------------------- 排序/汇总

_OK_ORDER = {"available": 0, "likely": 1, "unknown": 2, "blocked": 3, "unavailable": 4}


def sort_results(results: list[NodeResult]) -> list[NodeResult]:
    """按"好不好用"排序: 可用 > Gemini 优 > 链路快.

    排序用 rtt_min_ms (小请求往返最小值), 因为它排除了重定向与 body 下载,
    最能反映节点本身快慢; 缺失时回退到 latency.
    """
    def key(r: NodeResult):
        speed = r.rtt_min_ms or r.latency or 10**7
        return (
            0 if r.ok else 1,
            _OK_ORDER.get(r.gemini, 9),
            speed,
        )

    return sorted(results, key=key)


def summarize(results: list[NodeResult]) -> dict[str, Any]:
    total = len(results)
    ok = sum(1 for r in results if r.ok)
    google_ok = sum(1 for r in results if r.google == "success")
    gem_avail = sum(1 for r in results if r.gemini == "available")
    gem_likely = sum(1 for r in results if r.gemini == "likely")
    ag_avail = sum(1 for r in results if r.antigravity == "available")
    ag_likely = sum(1 for r in results if r.antigravity == "likely")
    risk_high = sum(1 for r in results
                    if str(getattr(r, "risk_level", "none")) == "high")

    # 延迟统计只计入链路真正可达的节点.
    # 否则像"剩余流量: xx GB"这种纯信息节点(并非代理)会以个位数毫秒混入,
    # 让"最快延迟"变成无意义的 1ms.
    def vals(attr: str) -> list[int]:
        return [v for r in results
                if r.reachable and (v := getattr(r, attr, 0)) and v > 0]

    rtts = vals("rtt_min_ms")
    ttfbs = vals("ttfb_ms")
    lats = vals("latency")

    from collections import Counter

    countries = Counter(r.country for r in results if r.country)
    protocols = Counter(r.protocol for r in results if r.protocol)

    return {
        "total": total,
        "usable": ok,
        "google_ok": google_ok,
        "gemini_available": gem_avail,
        "gemini_likely": gem_likely,
        "antigravity_available": ag_avail,
        "antigravity_likely": ag_likely,
        "risk_high": risk_high,
        # 三个延迟口径各自给出 均值/最快, 便于不同角度比较
        "avg_rtt_ms": int(sum(rtts) / len(rtts)) if rtts else 0,
        "min_rtt_ms": min(rtts) if rtts else 0,
        "avg_ttfb_ms": int(sum(ttfbs) / len(ttfbs)) if ttfbs else 0,
        "min_ttfb_ms": min(ttfbs) if ttfbs else 0,
        "avg_latency_ms": int(sum(lats) / len(lats)) if lats else 0,
        "min_latency_ms": min(lats) if lats else 0,
        "countries": dict(countries.most_common()),
        "protocols": dict(protocols.most_common()),
    }
