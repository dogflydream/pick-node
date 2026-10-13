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


# ---------------------------------------------------------------- 节点文本解析


def parse_nodes_text(text: str) -> list[ParsedNode]:
    """把多行文本解析成去重后的节点列表.

    规则 (与导入/检测共用, 保证行为一致):
      - 一行一个 URI
      - 跳过空行、# 与 // 开头的注释
      - 解析失败的整行忽略
      - 按 node_id 去重 (同一节点写多遍只保留一个)
    """
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
    return nodes


def analyze_import(text: str, existing_ids: set[str]) -> dict[str, Any]:
    """分析一段待导入的文本, 不改动任何文件.

    返回给前端预览用: 新增几条 / 重复几条 / 无效几行, 以及具体明细.
    这样用户能在真正写入前看清会发生什么。
    """
    new_nodes: list[ParsedNode] = []
    dup_lines: list[str] = []
    bad_lines: list[dict[str, str]] = []
    seen: set[str] = set()
    total_lines = 0

    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#") or s.startswith("//"):
            continue
        total_lines += 1
        n = parse_uri(s)
        if not n.ok:
            bad_lines.append({"line": s[:120], "error": n.error})
            continue
        # 与已有节点重复, 或本批内部重复
        if n.node_id in existing_ids or n.node_id in seen:
            dup_lines.append(s[:120])
            continue
        seen.add(n.node_id)
        new_nodes.append(n)

    return {
        "total_lines": total_lines,
        "new_count": len(new_nodes),
        "dup_count": len(dup_lines),
        "bad_count": len(bad_lines),
        "new_nodes": new_nodes,
        "dup_samples": dup_lines[:8],
        "bad_samples": bad_lines[:8],
    }


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
            nodes = parse_nodes_text(text)

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

    # ---------------------------------------------------------- 导入节点

    def existing_ids(self) -> set[str]:
        """节点文件里已有的 node_id 集合 (用于判重)."""
        if not self.nodes_file.is_file():
            return set()
        text = self.nodes_file.read_text(encoding="utf-8-sig", errors="replace")
        return {n.node_id for n in parse_nodes_text(text)}

    def preview_import(self, text: str) -> dict[str, Any]:
        """预览导入结果 (不写文件)."""
        return analyze_import(text, self.existing_ids())

    def import_nodes(self, text: str) -> dict[str, Any]:
        """把一段 URI 文本追加进节点文件, 自动去重.

        行为:
          - 解析每一行, 跳过空行/注释/无效行
          - 与【已有节点】和【本批内部】双重去重
          - 只把新增的追加到文件末尾, 不改动原有内容
          - 写文件用无 BOM UTF-8 (与内核配置一致, 避免解析问题)

        返回统计信息, 供前端提示"新增 X 条 / 重复 Y 条 / 无效 Z 行".
        """
        with self._lock:
            if self._state.status == "running":
                return {
                    "ok": False,
                    "error": "检测正在运行, 为避免文件被同时读写, 请等检测结束后再导入",
                }

            result = analyze_import(text, self.existing_ids())
            new_nodes: list[ParsedNode] = result["new_nodes"]

            if not new_nodes:
                return {
                    "ok": True,
                    "added": 0,
                    "dup_count": result["dup_count"],
                    "bad_count": result["bad_count"],
                    "total_after": len(self.existing_ids()),
                    "message": "没有需要新增的节点 (全部重复或无效)",
                    "dup_samples": result["dup_samples"],
                    "bad_samples": result["bad_samples"],
                }

            # ---- 追加写入: 保留原文件内容, 只补新增行
            old = ""
            if self.nodes_file.is_file():
                old = self.nodes_file.read_text(encoding="utf-8-sig", errors="replace")
                if old and not old.endswith("\n"):
                    old += "\n"

            added_lines = "\n".join(n.raw.strip() for n in new_nodes) + "\n"
            write_text_nobom(self.nodes_file, old + added_lines)

            total_after = len(self.existing_ids())
            return {
                "ok": True,
                "added": len(new_nodes),
                "dup_count": result["dup_count"],
                "bad_count": result["bad_count"],
                "total_after": total_after,
                "message": f"已导入 {len(new_nodes)} 个节点",
                "added_samples": [n.tag[:40] for n in new_nodes[:8]],
                "dup_samples": result["dup_samples"],
                "bad_samples": result["bad_samples"],
            }

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
