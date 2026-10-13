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


# ---------------------------------------------------------------- 分类结果源
#
# 单分类检测会把结果写到 results_<category>.json (见 main.write_outputs).
# 这里为每个分类维护独立的 ResultStore, 前端按分类切换视图.

_BASE_RESULTS = ROOT / _env("RESULTS_FILE", "results.json")
_stores: dict[str, ResultStore] = {}


def store_for(category: str = "") -> ResultStore:
    """取某分类的结果仓库; category 为空则用默认 (results.json)."""
    key = (category or "").strip().lower()
    if key not in _stores:
        if key:
            p = _BASE_RESULTS.with_name(f"results_{key}.json")
        else:
            p = _BASE_RESULTS
        _stores[key] = ResultStore(p)
    return _stores[key]


# 兼容旧调用点: 默认仓库
store = store_for("")


def available_categories() -> list[dict[str, Any]]:
    """列出分类及其结果文件是否存在, 供前端显示可切换的页签."""
    from core.categories import all_categories

    out: list[dict[str, Any]] = []
    labels = all_categories()
    for key, label in labels.items():
        st = store_for(key)
        rows = st.all()
        out.append({
            "key": key,
            "label": label,
            "file": st.path.name,
            "exists": st.path.is_file(),
            "count": len(rows),
        })
    return out


# ---------------------------------------------------------------- 依赖刷新

def _refresh() -> None:
    """每次请求前检查文件是否被测试进程更新过."""
    store.reload_if_changed()


# ---------------------------------------------------------------- API


@app.get("/api/categories")
def api_categories() -> dict[str, Any]:
    """列出所有分类及其结果文件状态 (供前端页签)."""
    cats = available_categories()
    return {"categories": cats, "default": cats[0]["key"] if cats else ""}


@app.get("/api/summary")
def api_summary(category: str = "") -> dict[str, Any]:
    """汇总统计 (指标卡 + 地区分布). category 为空用默认结果文件."""
    st = store_for(category)
    st.reload_if_changed()
    return st.summary()


@app.get("/api/facets")
def api_facets(category: str = "") -> dict[str, Any]:
    """筛选项候选值 (国家/协议/可用性/分类) 及计数."""
    st = store_for(category)
    st.reload_if_changed()
    return st.facets()


@app.get("/api/nodes")
def api_nodes(
    search: str = "",
    protocol: str = "",
    country: str = "",
    gemini: str = "",
    antigravity: str = "",
    risk: str = "",
    category: str = "",
    cat_verdict: str = "",
    usable_only: bool = False,
    sort: str = "default",
    order: str = "asc",
    limit: int = Query(2000, ge=1, le=20000),
) -> dict[str, Any]:
    """查询节点列表.

    category:    数据源分类 (xai / google); 空则用默认结果文件
    cat_verdict: 该分类下的结论筛选 (available / blocked / unavailable)
    gemini / antigravity: 旧版 Google 维度筛选 (保留兼容)
    risk: high / low / none —— 按"是否被判定为代理/VPN 出口"筛选.
    """
    st = store_for(category)
    st.reload_if_changed()
    rows = st.query(
        search=search, protocol=protocol, country=country, gemini=gemini,
        antigravity=antigravity, risk=risk, category="",
        cat_verdict="", usable_only=usable_only,
        sort=sort, order=order,
    )
    # 分类结论筛选在第 1 个参数上做 (store 已按 category 过滤数据源)
    if cat_verdict:
        from core.store import category_verdict as _cv
        rows = [r for r in rows if _cv(r, category) == cat_verdict]
    total = len(rows)
    return {"total": total, "items": rows[:limit]}


@app.get("/api/meta")
def api_meta(category: str = "") -> dict[str, Any]:
    """数据源元信息, 供页面显示"数据更新时间"."""
    st = store_for(category)
    st.reload_if_changed()
    p = st.path
    mtime = None
    exists = p.is_file()
    if exists:
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            mtime = None
    # 已导入的节点数 —— 前端据此判断是"还没导入"还是"导入了但没测"
    try:
        node_count = len(retest.existing_ids())
    except Exception:
        node_count = 0

    return {
        "file": p.name,
        "exists": exists,
        "updated_at": mtime,
        "count": len(st.all()),
        "error": st.error,
        "node_count": node_count,
        "category": (category or "").lower(),
        # 供前端确认条显示当前默认配置
        "concurrency": retest.concurrency,
        "nodes_file": retest.nodes_file.name,
        "pool_file": retest.pool_file.name,
    }


@app.get("/api/export", response_class=PlainTextResponse)
def api_export(
    scope: str = Query("usable", pattern="^(usable|strict|all)$"),
    ids: str = "",
    category: str = "",
    download: bool = False,
) -> Any:
    """导出节点 URI, 一行一个.

    scope=usable (默认) 只导出"潜在可用"节点.
    category: 从哪个分类的结果里导出 (xai / google).
    """
    st = store_for(category)
    st.reload_if_changed()
    id_list = [x for x in (ids or "").split(",") if x.strip()] or None
    uris = st.export_uris(scope=scope, ids=id_list)
    if not uris:
        raise HTTPException(status_code=404, detail="没有符合条件的节点")

    body = "\n".join(uris) + "\n"
    if download:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = {"usable": "usable", "strict": "strict", "all": "all"}.get(scope, scope)
        if category:
            name = f"{category}_{name}"
        return StreamingResponse(
            io.BytesIO(body.encode("utf-8")),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="nodes_{name}_{stamp}.txt"'},
        )
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8")


@app.post("/api/reload")
def api_reload(category: str = "") -> dict[str, Any]:
    """强制重新读取结果文件 (测试跑完后手动刷新)."""
    st = store_for(category)
    st.load()
    return {"ok": True, "count": len(st.all()), "error": st.error}


# ---------------------------------------------------------------- 节点导入


@app.post("/api/nodes/preview")
def api_nodes_preview(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """预览导入结果 (不写文件).

    body: {"text": "<多行 URI>"}
    返回 新增/重复/无效 的条数与样例, 供前端在提交前展示。
    """
    text = str((payload or {}).get("text", "") or "")
    if not text.strip():
        return {"total_lines": 0, "new_count": 0, "dup_count": 0,
                "bad_count": 0, "dup_samples": [], "bad_samples": []}
    info = retest.preview_import(text)
    info.pop("new_nodes", None)   # 不把内部对象返回给前端
    return info


@app.post("/api/nodes/import")
def api_nodes_import(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """把 URI 追加导入节点文件, 自动去重.

    body: {"text": "<多行 URI>"}
    """
    text = str((payload or {}).get("text", "") or "")
    if not text.strip():
        raise HTTPException(status_code=400, detail="没有可导入的内容")
    result = retest.import_nodes(text)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "导入失败"))
    return result


# ---------------------------------------------------------------- 重新检测


@app.post("/api/retest")
def api_retest_start(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """启动一次重新检测.

    请求体 (都可选):
      node_ids    : 要重测的 node_id 列表 (网页端勾选的节点)
      concurrency : 覆盖并发数
      limit       : 只测前 N 个 (调试用)
      categories  : 要检测的分类列表 (如 ["xai"]); 不传则用默认

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

    cats = body.get("categories")
    if isinstance(cats, str):
        cats = [c.strip() for c in cats.split(",") if c.strip()]
    if cats:
        from core.categories import REGISTRY
        unknown = [c for c in cats if c not in REGISTRY]
        if unknown:
            raise HTTPException(status_code=400,
                                detail=f"未知分类: {unknown}")

    ok, msg = retest.start(node_ids=node_ids, scope="all",
                           concurrency=concurrency, limit=limit,
                           categories=cats)
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


def _port_free(host: str, port: int) -> bool:
    """检查端口是否可绑定."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _pick_port(host: str, preferred: int) -> int:
    """优先用指定端口; 被占用则自动往后找一个可用的.

    这样用户不会因为 8787 被占 (比如上一次没关干净) 就卡住,
    这也是实测中遇到过的摩擦点。
    """
    if _port_free(host, preferred):
        return preferred
    for p in range(preferred + 1, preferred + 20):
        if _port_free(host, p):
            print(f"  [提示] 端口 {preferred} 已被占用, 自动改用 {p}")
            return p
    return preferred   # 都占用了就交给 uvicorn 报错


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
    result_count = len(store.all())
    try:
        node_count = len(retest.existing_ids())
    except Exception:
        node_count = 0

    port = _pick_port(args.host, args.port)

    print("=" * 72)
    print("  代理节点体检面板")
    print("=" * 72)
    print(f"  访问地址 : http://{args.host}:{port}")
    print(f"  节点文件 : {retest.nodes_file}  (已导入 {node_count} 个节点)")
    print(f"  结果文件 : {store.path}  ({result_count} 条结果)")

    # 首次使用引导: 明确告诉用户接下来在网页里做什么, 而不是让他去跑命令行
    if result_count == 0:
        print("-" * 72)
        print("  还没有检测结果. 直接用浏览器打开上面的地址:")
        print("    1) 右上角点「导入节点」, 粘贴代理链接 (一行一个)")
        print("    2) 点「重新检测」开始体检")
        print("    3) 测完可一键导出可用节点")
        if node_count == 0:
            print()
            print("  提示: 你也可以直接把节点写进上面那个节点文件, 再刷新页面.")
    if args.host not in ("127.0.0.1", "localhost"):
        print("  [警告] 已监听非本机地址, 结果含节点凭据, 请勿在公网暴露")
    print("=" * 72)

    uvicorn.run(
        "web.server:app" if args.reload else app,
        host=args.host,
        port=port,
        reload=args.reload,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
