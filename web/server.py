"""Web 面板后端 (FastAPI).

提供只读的数据查询接口与 URI 导出接口, 并托管前端静态页面.

安全: 默认只监听 127.0.0.1. 面板仅本机可见, 不对外暴露,
     因为结果里包含出口 IP 与节点凭据.
"""

from __future__ import annotations

import io
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# 允许以 `python web/server.py` 直接运行
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json  # noqa: E402
import os  # noqa: E402

from core.retest import RetestManager  # noqa: E402
from core.store import ResultStore  # noqa: E402
from core.util import setup_console  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="代理节点体检面板",
    description="查看代理节点检测结果, 触发重新检测, 并导出可用节点 URI",
    version="1.1.0",
)

store = ResultStore(ROOT / "results.json")


# ---------------------------------------------------------------- 配置读取
#
# 复用 .env 里的配置, 让网页端重测与 CLI 行为一致.
# 这里只读不写, 不引入额外依赖.


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _env(key: str, default: str) -> str:
    return (os.environ.get(key, "") or "").strip() or default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    v = _env(key, "").lower()
    return default if not v else v in ("1", "true", "yes", "on", "y")


_load_env_file(ROOT / ".env")

retest = RetestManager(
    root=ROOT,
    nodes_file=ROOT / _env("NODES_FILE", "proxiers.txt"),
    results_file=ROOT / _env("RESULTS_FILE", "results.json"),
    pool_file=ROOT / _env("POOL_FILE", "proxiers_google.txt"),
    concurrency=_env_int("CONCURRENCY", 10),
    timeout=float(_env("REQUEST_TIMEOUT", "15")),
    start_timeout=float(_env("START_TIMEOUT", "12")),
    kernel=_env("KERNEL", "auto"),
    check_antigravity=_env_bool("CHECK_ANTIGRAVITY", True),
    rtt_rounds=_env_int("RTT_ROUNDS", 3),
)


# ---------------------------------------------------------------- 依赖刷新

def _refresh() -> None:
    """每次请求前检查文件是否被测试进程更新过."""
    store.reload_if_changed()


# ---------------------------------------------------------------- API


@app.get("/api/summary")
def api_summary() -> dict[str, Any]:
    """汇总统计 (指标卡 + 地区分布)."""
    _refresh()
    return store.summary()


@app.get("/api/facets")
def api_facets() -> dict[str, Any]:
    """筛选项候选值 (国家/协议/可用性) 及计数."""
    _refresh()
    return store.facets()


@app.get("/api/nodes")
def api_nodes(
    search: str = "",
    protocol: str = "",
    country: str = "",
    gemini: str = "",
    risk: str = "",
    usable_only: bool = False,
    sort: str = "default",
    order: str = "asc",
    limit: int = Query(2000, ge=1, le=20000),
) -> dict[str, Any]:
    """查询节点列表.

    risk: high / low / none —— 按"是否被 Google 判定为代理/VPN 出口"筛选.
    """
    _refresh()
    rows = store.query(
        search=search, protocol=protocol, country=country, gemini=gemini,
        risk=risk, usable_only=usable_only, sort=sort, order=order,
    )
    total = len(rows)
    return {"total": total, "items": rows[:limit]}


@app.get("/api/meta")
def api_meta() -> dict[str, Any]:
    """数据源元信息, 供页面显示"数据更新时间"."""
    _refresh()
    p = store.path
    mtime = None
    exists = p.is_file()
    if exists:
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            mtime = None
    return {
        "file": p.name,
        "exists": exists,
        "updated_at": mtime,
        "count": len(store.all()),
        "error": store.error,
        # 供前端确认条显示当前默认配置
        "concurrency": retest.concurrency,
        "nodes_file": retest.nodes_file.name,
        "pool_file": retest.pool_file.name,
    }


@app.get("/api/export", response_class=PlainTextResponse)
def api_export(
    scope: str = Query("usable", pattern="^(usable|strict|all)$"),
    ids: str = "",
    download: bool = False,
) -> Any:
    """导出节点 URI, 一行一个.

    scope=usable (默认) 只导出"潜在可用"节点:
      链路可达 + Google 成功 + Gemini 未被明确拒绝(available/likely)
    """
    _refresh()
    id_list = [x for x in (ids or "").split(",") if x.strip()] or None
    uris = store.export_uris(scope=scope, ids=id_list)
    if not uris:
        raise HTTPException(status_code=404, detail="没有符合条件的节点")

    body = "\n".join(uris) + "\n"
    if download:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = {"usable": "usable", "strict": "strict", "all": "all"}.get(scope, scope)
        return StreamingResponse(
            io.BytesIO(body.encode("utf-8")),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="nodes_{name}_{stamp}.txt"'},
        )
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8")


@app.post("/api/reload")
def api_reload() -> dict[str, Any]:
    """强制重新读取结果文件 (测试跑完后手动刷新)."""
    store.load()
    return {"ok": True, "count": len(store.all()), "error": store.error}


# ---------------------------------------------------------------- 重新检测


@app.post("/api/retest")
def api_retest_start(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """启动一次重新检测.

    请求体 (都可选):
      node_ids    : 要重测的 node_id 列表 (网页端勾选的节点)
      concurrency : 覆盖并发数
      limit       : 只测前 N 个 (调试用)

    不传 node_ids 时重测 nodes_file 里的全部节点.
    """
    body = payload or {}
    node_ids = body.get("node_ids") or None
    if isinstance(node_ids, str):
        node_ids = [x for x in node_ids.split(",") if x.strip()] or None
    concurrency = body.get("concurrency")
    try:
        concurrency = int(concurrency) if concurrency else None
    except (TypeError, ValueError):
        concurrency = None
    limit = body.get("limit")
    try:
        limit = int(limit) if limit else None
    except (TypeError, ValueError):
        limit = None

    ok, msg = retest.start(node_ids=node_ids, scope="all",
                           concurrency=concurrency, limit=limit)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "message": msg, "state": retest.state().snapshot(False)}


@app.get("/api/retest/status")
def api_retest_status(since: int = 0) -> dict[str, Any]:
    """查询当前作业状态. since 用于只取新增的结果 (轮询降级方案)."""
    return retest.state().snapshot(include_results=True, since=max(0, since))


@app.post("/api/retest/cancel")
def api_retest_cancel() -> dict[str, Any]:
    """取消正在运行的检测."""
    ok, msg = retest.cancel()
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "message": msg}


@app.get("/api/retest/events")
def api_retest_events(since: int = 0) -> StreamingResponse:
    """SSE 实时推送检测进度.

    事件类型:
      snapshot —— 初始状态
      progress —— 进度与新增结果
      done     —— 作业结束 (含 summary)
    前端用 EventSource 订阅.
    """
    import time as _time

    start_since = max(0, since)

    def gen():
        seen = start_since
        last_sig = None
        idle = 0
        # 最长 30 分钟, 防止连接永久挂着
        deadline = _time.time() + 1800
        while _time.time() < deadline:
            st = retest.state()
            snap = st.snapshot(include_results=True, since=seen)
            seen = len(st.results)
            # 只在有变化时推送, 减少无用流量
            sig = (st.status, st.done, len(st.log))
            if sig != last_sig:
                last_sig = sig
                yield f"event: progress\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
                idle = 0
            else:
                idle += 1
                # 心跳, 保持连接不被中间层断开
                if idle % 25 == 0:
                    yield ": keepalive\n\n"
            if st.status in ("done", "cancelled", "failed"):
                yield (f"event: done\ndata: "
                       f"{json.dumps(snap, ensure_ascii=False)}\n\n")
                break
            _time.sleep(0.4)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


# ---------------------------------------------------------------- 静态页面


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    f = STATIC_DIR / "index.html"
    if not f.is_file():
        raise HTTPException(status_code=500, detail="缺少前端文件 web/static/index.html")
    return HTMLResponse(f.read_text(encoding="utf-8"))


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------- 入口


def main() -> int:
    setup_console()
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="代理节点体检 Web 面板")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址 (默认仅本机; 设为 0.0.0.0 会暴露到局域网)")
    ap.add_argument("--port", type=int, default=8787, help="监听端口")
    ap.add_argument("--reload", action="store_true", help="代码改动自动重载(开发用)")
    args = ap.parse_args()

    store.load()
    n = len(store.all())
    print("=" * 72)
    print("  代理节点体检面板")
    print("=" * 72)
    print(f"  数据文件 : {store.path}")
    print(f"  节点数量 : {n}" + (f"   (注意: {store.error})" if store.error else ""))
    print(f"  访问地址 : http://{args.host}:{args.port}")
    if args.host not in ("127.0.0.1", "localhost"):
        print("  [警告] 已监听非本机地址, 结果含节点凭据, 请勿在公网暴露")
    print("=" * 72)

    uvicorn.run(
        "web.server:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
