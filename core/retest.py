"""网页端「重新检测」作业管理.

设计要点:
  - 后台线程跑检测, 不阻塞 Web 请求; 前端通过 SSE 实时收进度.
  - 只允许同时跑一个作业 (避免多个批次抢内核进程与端口).
  - 结果合并写回 results.json: 只覆盖本次重测到的节点, 其余节点保留.
  - 支持取消: 用一个 threading.Event 通知线程尽快收尾, 并回收子进程.

为什么放在 core 而不是 web:
  它是"检测编排"能力, 与 FastAPI 无关, 方便单独测试与将来被 CLI 复用.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .launcher import Launcher
from .parser import ParsedNode, parse_uri
from .tester import NodeResult, sort_results, summarize
from .util import find_kernel, write_text_nobom


# ---------------------------------------------------------------- 作业状态


@dataclass
class JobState:
    """一个重测作业的实时状态 (前端轮询/SSE 读它)."""

    id: str = ""
    status: str = "idle"          # idle / running / done / cancelled / failed
    total: int = 0
    done: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    concurrency: int = 0
    scope: str = "all"            # all / selected
    # 每条完成的结果 (给前端实时上色)
    results: list[dict[str, Any]] = field(default_factory=list)
    # 进度日志
    log: list[str] = field(default_factory=list)
    error: str = ""
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1)

    def snapshot(self, include_results: bool = True, since: int = 0) -> dict[str, Any]:
        """给前端的快照. since 用于只取新增的结果 (增量推送)."""
        d: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "elapsed": self.elapsed,
            "scope": self.scope,
            "concurrency": self.concurrency,
            "error": self.error,
            "summary": self.summary,
            "log": self.log[-40:],
            "result_count": len(self.results),
        }
        if include_results:
            d["results"] = self.results[since:]
        return d


# ---------------------------------------------------------------- 管理器


class RetestManager:
    """管理网页端触发的重测作业."""

    def __init__(
        self,
        root: Path,
        nodes_file: Path,
        results_file: Path,
        pool_file: Path,
        concurrency: int = 10,
        timeout: float = 15.0,
        start_timeout: float = 12.0,
        kernel: str = "auto",
        check_antigravity: bool = True,
        rtt_rounds: int = 3,
    ):
        self.root = Path(root)
        self.nodes_file = Path(nodes_file)
        self.results_file = Path(results_file)
        self.pool_file = Path(pool_file)
        self.concurrency = concurrency
        self.timeout = timeout
        self.start_timeout = start_timeout
        self.kernel = kernel
        self.check_antigravity = check_antigravity
        self.rtt_rounds = max(1, rtt_rounds)

        self._lock = threading.RLock()
        self._state = JobState()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        # 版本号: 每次作业开始 +1, 让前端能分辨是不同的作业
        self._seq = 0

    # ---------------------------------------------------------- 状态查询

    def state(self) -> JobState:
        with self._lock:
            return self._state

    def is_running(self) -> bool:
        with self._lock:
            return self._state.status == "running"

    # ---------------------------------------------------------- 启动

    def start(
        self,
        node_ids: list[str] | None = None,
        scope: str = "all",
        concurrency: int | None = None,
        limit: int | None = None,
    ) -> tuple[bool, str]:
        """启动重测. 返回 (是否启动成功, 说明).

        node_ids: 指定要重测的 node_id 列表 (网页端勾选的节点).
        scope:    all / selected, 仅用于展示.
        """
        with self._lock:
            if self._state.status == "running":
                return False, "已有检测任务在运行, 请等待完成或先取消"

            # ---- 读取并解析节点
            if not self.nodes_file.is_file():
                return False, f"找不到节点文件: {self.nodes_file.name}"
            text = self.nodes_file.read_text(encoding="utf-8-sig", errors="replace")
            nodes: list[ParsedNode] = []
            seen: set[str] = set()
            for line in text.splitlines():
                s = line.strip()
                if not s or s.startswith("#") or s.startswith("//"):
                    continue
                n = parse_uri(s)
                if not n.ok or n.node_id in seen:
                    continue
                seen.add(n.node_id)
                nodes.append(n)

            if not nodes:
                return False, "节点文件中没有可解析的节点"

            # ---- 按勾选过滤
            if node_ids:
                want = set(node_ids)
                nodes = [n for n in nodes if n.node_id in want]
                if not nodes:
                    return False, "勾选的节点不在当前节点文件中"
                scope = "selected"

            if limit and limit > 0:
                nodes = nodes[:limit]

            # ---- 重置状态
            self._seq += 1
            self._cancel = threading.Event()
            self._state = JobState(
                id=f"job{self._seq}-{datetime.now().strftime('%H%M%S')}",
                status="running",
                total=len(nodes),
                started_at=time.time(),
                scope=scope,
                concurrency=concurrency or self.concurrency,
            )
            self._state.log.append(
                f"开始检测 {len(nodes)} 个节点, 并发 {self._state.concurrency}"
            )

        self._thread = threading.Thread(
            target=self._run, args=(nodes,), name="retest", daemon=True
        )
        self._thread.start()
        return True, "已开始"

    def cancel(self) -> tuple[bool, str]:
        """请求取消当前作业."""
        with self._lock:
            if self._state.status != "running":
                return False, "当前没有运行中的任务"
            self._cancel.set()
            self._state.log.append("收到取消请求, 正在收尾...")
        return True, "正在取消"

    # ---------------------------------------------------------- 执行体

    def _run(self, nodes: list[ParsedNode]) -> None:
        """在后台线程里执行检测并写回结果."""
        launcher = None
        try:
            # ---- 定位内核
            from .detector import Detector
            from .tester import Tester

            search_root = None
            env_bin = __import__("os").environ.get("V2RAYN_BIN", "").strip()
            if env_bin:
                search_root = env_bin
            singbox = find_kernel("sing-box", search_root)
            xray = find_kernel("xray", search_root)
            if not singbox and not xray:
                raise RuntimeError("未找到 sing-box.exe / xray.exe")

            launcher = Launcher(
                workdir=self.root, singbox=singbox, xray=xray,
                kernel_preference=self.kernel,
            )
            detector = Detector(timeout=self.timeout,
                                do_antigravity=self.check_antigravity,
                                rtt_rounds=self.rtt_rounds)

            def on_event(event: str, payload: dict) -> None:
                if event == "result":
                    with self._lock:
                        self._state.results.append(payload)
                        self._state.done = len(self._state.results)
                elif event == "progress":
                    pass  # 进度由 results 数量推导

            tester = Tester(
                launcher=launcher, detector=detector,
                concurrency=self._state.concurrency,
                start_timeout=self.start_timeout,
                on_event=on_event,
                should_stop=self._cancel.is_set,
            )

            results = tester.run(nodes)

            # ---- 合并写回
            with self._lock:
                cancelled = self._cancel.is_set()
                self._state.log.append(
                    ("已取消, " if cancelled else "")
                    + f"完成 {len(results)} 个节点, 正在写回结果"
                )

            merged, added = self._merge_and_write(results)

            with self._lock:
                # 汇总要反映"合并后的整体情况" (而不只是本次批次),
                # 所以把 dict 还原成 NodeResult 再交给 summarize().
                restored = [NodeResult(**r) for r in merged if _is_node_result(r)]
                self._state.summary = summarize(restored)
                self._state.status = "cancelled" if cancelled else "done"
                self._state.finished_at = time.time()
                self._state.log.append(
                    f"已写回 results.json (合并后共 {len(merged)} 条, "
                    f"本次更新 {added} 条)"
                )

        except Exception as e:
            with self._lock:
                self._state.status = "failed"
                self._state.error = f"{type(e).__name__}: {e}"
                self._state.finished_at = time.time()
                self._state.log.append(f"失败: {self._state.error}")
        finally:
            # 关键: 无论成功失败, 都要回收内核子进程
            if launcher is not None:
                try:
                    launcher.shutdown_all()
                except Exception:
                    pass

    # ---------------------------------------------------------- 合并写回

    def _merge_and_write(self, results: list[NodeResult]) -> tuple[list[dict], int]:
        """把本次结果合并进 results.json, 返回 (全部条目, 本次更新数).

        合并规则: 以 node_id 为键, 本次测到的覆盖旧的; 其余节点原样保留.
        这样"只重测勾选的几个节点"不会把其它节点的历史结果冲掉.
        """
        old: list[dict] = []
        if self.results_file.is_file():
            try:
                raw = json.loads(
                    self.results_file.read_text(encoding="utf-8", errors="replace")
                )
                if isinstance(raw, list):
                    old = [r for r in raw if isinstance(r, dict)]
            except (json.JSONDecodeError, OSError):
                old = []

        new_by_id = {r.node_id: r.to_dict() for r in results}
        merged: list[dict] = []
        replaced: set[str] = set()

        for row in old:
            nid = str(row.get("node_id", ""))
            if nid in new_by_id:
                merged.append(new_by_id[nid])   # 用新结果覆盖
                replaced.add(nid)
            else:
                merged.append(row)              # 保留旧结果

        # 旧文件里没有的节点 (例如新导入的) 追加进来
        for nid, row in new_by_id.items():
            if nid not in replaced:
                merged.append(row)

        ordered = sort_results([NodeResult(**r) for r in merged
                                if _is_node_result(r)])
        payload = [r.to_dict() for r in ordered]
        write_text_nobom(self.results_file,
                         json.dumps(payload, ensure_ascii=False, indent=2))

        # 代理池与 CLI 保持一致: 只写潜在可用
        # 注意: ordered 里已经是 dict (NodeResult.to_dict 的结果), 不能再调 to_dict()
        from .store import is_potentially_usable
        usable = [str(r.get("node", "")).strip() for r in payload
                  if is_potentially_usable(r) and str(r.get("node", "")).strip()]
        write_text_nobom(self.pool_file,
                         "\n".join(usable) + ("\n" if usable else ""))

        return payload, len(new_by_id)


def _is_node_result(row: dict) -> bool:
    """判断旧文件里的一条记录能否还原成 NodeResult (字段兼容)."""
    try:
        NodeResult(**row)
        return True
    except TypeError:
        return False
