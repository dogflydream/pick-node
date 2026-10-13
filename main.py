"""代理节点批量体检工具 —— 命令行入口.

用法 (Windows):
    .venv\\Scripts\\python.exe main.py                     # 用 .env 与默认文件名
    .venv\\Scripts\\python.exe main.py -c 20               # 20 并发
    .venv\\Scripts\\python.exe main.py -i proxiers.txt     # 指定输入
    .venv\\Scripts\\python.exe main.py --only-usable       # 只保留可用节点

输入: 一行一个代理 URI (proxiers.txt)
输出: results.json 全量结果 + proxiers_google.txt 可用代理池
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.detector import Detector, country_name
from core.launcher import Launcher
from core.parser import ParsedNode, parse_many
from core.tester import NodeResult, sort_results, summarize
from core.util import find_kernel, kernel_version, setup_console, write_text_nobom

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- .env


def load_env(path: Path) -> None:
    """极简 .env 加载器 (不引入额外依赖).

    - 支持 KEY=VALUE
    - 支持 # 注释与空行
    - 支持引号包裹的值
    - 已存在的环境变量优先, 不覆盖 (便于命令行临时覆盖)
    """
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
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def env_path(key: str, default: str) -> str:
    v = os.environ.get(key, "").strip()
    return v or default


def env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "").strip() or default)
    except ValueError:
        return default


def env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on", "y")


# ---------------------------------------------------------------- 输出


class Reporter:
    """CLI 进度与结果输出."""

    def __init__(self, verbose: bool = True, stream=None):
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self._lock = threading.Lock()
        self._t0 = time.time()
        self._counts = {"ok": 0, "fail": 0}

    def _w(self, s: str) -> None:
        with self._lock:
            print(s, file=self.stream, flush=True)

    def header(self, total: int, concurrency: int, kernels: dict) -> None:
        self._w("=" * 96)
        self._w("  代理节点批量体检工具  --  出口地区 / Google / Gemini / Antigravity")
        self._w("=" * 96)
        for name, exe in kernels.items():
            if exe:
                self._w(f"  {name:9} : {exe}")
                self._w(f"  {'':9}   {kernel_version(exe)}")
            else:
                self._w(f"  {name:9} : [未找到]")
        self._w(f"  待测节点   : {total}")
        self._w(f"  并发数     : {concurrency}")
        self._w("=" * 96)

    def progress(self, done: int, total: int, tag: str) -> None:
        if not self.verbose:
            return
        bar_len = 28
        filled = int(bar_len * done / max(1, total))
        bar = "#" * filled + "." * (bar_len - filled)
        pct = 100.0 * done / max(1, total)
        self._w(f"  [{bar}] {done}/{total} ({pct:5.1f}%)")

    def result(self, r: NodeResult, index: int, total: int) -> None:
        """输出单节点结果行.

        延迟列用 rtt_min_ms (小请求往返最小值) —— 它最接近"节点快不快",
        而不是混入了 Google 首页 body 下载的 latency.
        """
        cc = r.country or "--"
        rtt = f"{r.rtt_min_ms}ms" if r.rtt_min_ms else (
            f"{r.latency}ms" if r.latency else "-")
        if r.error and not r.reachable:
            self._w(f"[FAIL] {r.protocol:9} {cc:2} {rtt:>8}  {r.tag[:36]}  "
                    f"<{r.error[:60]}>")
            return
        if r.ok:
            # 有分类结果时展示分类结论, 否则回退到 Google 维度
            if r.categories:
                parts = " ".join(
                    f"{k.upper()} {str(v.get('verdict', '?')).upper()[:9]}"
                    for k, v in r.categories.items())
                self._w(f"[OK]   {r.protocol:9} {cc:2} {rtt:>8}  {r.tag[:34]:36}  {parts}")
            else:
                gem = r.gemini.upper()[:9]
                ag = r.antigravity.upper()[:9]
                self._w(f"[OK]   {r.protocol:9} {cc:2} {rtt:>8}  {r.tag[:36]}  "
                        f"Gemini {gem:9} Antigravity {ag}")
        else:
            if r.categories:
                # 取第一个非 available 的分类原因
                reason = next(
                    (str(v.get("reason", "")) for v in r.categories.values()
                     if v.get("verdict") != "available"),
                    "不可用")
            else:
                reason = r.gemini_reason or r.google or "不可用"
            self._w(f"[NO]   {r.protocol:9} {cc:2} {rtt:>8}  {r.tag[:36]}  "
                    f"<{reason[:56]}>")

    def footer(self, summary: dict, elapsed: float) -> None:
        s = summary
        self._w("=" * 96)
        self._w("  汇总")
        self._w("=" * 96)
        self._w(f"  总节点        : {s['total']}")
        self._w(f"  综合可用      : {s['usable']}")

        # 有分类结果时, 按分类给结论 (只展示本次检测的分类)
        cats = s.get("categories") or {}
        if cats:
            from core.categories import all_categories
            labels = all_categories()
            for key, st in cats.items():
                label = labels.get(key, key.upper())
                self._w(f"  {label:^14}: 可用 {st.get('available', 0)}  "
                        f"受限 {st.get('blocked', 0)}  "
                        f"不可用 {st.get('unavailable', 0)}")
                for sk, sv in (st.get("services") or {}).items():
                    detail = "  ".join(f"{k} {v}" for k, v in sv.items())
                    self._w(f"      {sk:12}: {detail}")
        else:
            self._w(f"  Google 可达   : {s['google_ok']}")
            self._w(f"  Gemini 可用   : {s['gemini_available']}  (可能可用 {s['gemini_likely']})")
            self._w(f"  Antigravity   : {s['antigravity_available']}  (跟随 Gemini)")

        if s.get("risk_high"):
            self._w(f"  高风险(代理/VPN): {s['risk_high']}")
        # 三个延迟口径分别展示, 避免把"含 body 下载"的数字误当节点速度
        self._w(f"  链路 RTT      : 平均 {s.get('avg_rtt_ms', 0)} ms   "
                f"最快 {s.get('min_rtt_ms', 0)} ms      <- 推荐比较口径")
        # 首字节/端到端 只在测 GOOGLE 时有意义 (它们量的是 google.com)
        if not cats:
            self._w(f"  Google 首字节 : 平均 {s.get('avg_ttfb_ms', 0)} ms   "
                    f"最快 {s.get('min_ttfb_ms', 0)} ms")
            self._w(f"  Google 端到端 : 平均 {s.get('avg_latency_ms', 0)} ms   "
                    f"最快 {s.get('min_latency_ms', 0)} ms   (含下载首页)")
        if s["countries"]:
            tops = ", ".join(f"{k}:{v}" for k, v in list(s["countries"].items())[:12])
            self._w(f"  出口地区分布  : {tops}")
        if s["protocols"]:
            tops = ", ".join(f"{k}:{v}" for k, v in s["protocols"].items())
            self._w(f"  协议分布      : {tops}")
        self._w(f"  总耗时        : {elapsed:.1f} s")
        self._w("=" * 96)


# ---------------------------------------------------------------- 输出文件


def write_outputs(
    results: list[NodeResult],
    results_path: Path,
    pool_path: Path,
    only_usable: bool,
    pool_suffix: str = "",
) -> int:
    """写 results.json 与 代理池文件, 返回写入代理池的节点数.

    pool_suffix: 分类标识 (如 "xai"); 非空时池文件名会带上它,
                 避免测 XAI 的结果覆盖掉 GOOGLE 的代理池.
    """
    ordered = sort_results(results)

    # results.json: 全量明细 (按分类分文件, 避免互相覆盖)
    if pool_suffix and results_path.name == "results.json":
        results_path = results_path.with_name(f"results_{pool_suffix}.json")
    payload = [r.to_dict() for r in ordered]
    write_text_nobom(results_path, json.dumps(payload, ensure_ascii=False, indent=2))

    # 代理池: 只要可用的原始 URI (可直接被其他工具消费)
    if pool_suffix:
        pool_path = pool_path.with_name(f"proxiers_{pool_suffix}.txt")
    usable = [r for r in ordered if r.ok]
    lines = []
    for r in usable:
        lines.append(r.node.strip())
    write_text_nobom(pool_path, "\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


# ---------------------------------------------------------------- main


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="代理节点批量体检工具 (Windows, 基于 sing-box / xray)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python main.py                     使用 .env 中的配置\n"
               "  python main.py -c 20               20 个并发\n"
               "  python main.py -i my_nodes.txt     指定输入文件\n"
               "  python main.py --limit 10          只测前 10 个\n"
               "  python main.py --kernel xray       强制使用 xray\n",
    )
    p.add_argument("-i", "--input", help="节点文件 (一行一个 URI)")
    p.add_argument("-o", "--output", help="结果 JSON 输出路径")
    p.add_argument("--pool", help="可用节点代理池输出路径")
    p.add_argument("-c", "--concurrency", type=int, help="最大并发实例数")
    p.add_argument("--timeout", type=float, help="单个请求超时 (秒)")
    p.add_argument("--start-timeout", type=float, help="等待实例监听超时 (秒)")
    p.add_argument("--limit", type=int, help="只测试前 N 个节点")
    p.add_argument("--kernel", choices=["auto", "sing-box", "xray"],
                   help="内核选择策略")
    p.add_argument("--category", "-C", action="append", default=None,
                   metavar="KEY",
                   help="只检测指定分类 (可重复): xai / google. "
                        "不指定则按配置或全部分类")
    p.add_argument("--list-categories", action="store_true",
                   help="列出所有分类后退出")
    p.add_argument("--no-antigravity", action="store_true",
                   help="跳过 Antigravity 检测 (更快)")
    p.add_argument("--only-usable", action="store_true",
                   help="代理池只写入综合可用节点")
    p.add_argument("-q", "--quiet", action="store_true", help="精简输出")
    p.add_argument("--keep-configs", action="store_true",
                   help="保留生成的临时配置文件 (默认保留, 便于排查)")
    p.add_argument("--clean-configs", action="store_true",
                   help="测试结束后清理临时配置")
    return p


def main(argv: list[str] | None = None) -> int:
    setup_console()
    load_env(HERE / ".env")

    args = build_argparser().parse_args(argv)

    # ---- 列出分类
    if args.list_categories:
        from core.categories import all_categories
        cats = all_categories()
        print("可用分类:")
        for k, label in cats.items():
            print(f"  {k:10} {label}")
        return 0

    # ---- 分类选择: 命令行 > .env > 默认 (全部)
    from core.categories import REGISTRY, all_categories
    if args.category:
        chosen = list(dict.fromkeys(args.category))   # 去重保序
    else:
        env_cat = env_path("CATEGORIES", "").strip()
        chosen = [c.strip() for c in env_cat.split(",") if c.strip()]
    unknown = [c for c in chosen if c not in REGISTRY]
    if unknown:
        print(f"[错误] 未知分类: {unknown}", file=sys.stderr)
        print(f"       可用: {list(all_categories())}", file=sys.stderr)
        return 2

    # ---- 配置合并: 命令行 > .env > 默认
    in_file = Path(args.input or env_path("NODES_FILE", "proxiers.txt"))
    if not in_file.is_absolute():
        in_file = HERE / in_file
    out_file = Path(args.output or env_path("RESULTS_FILE", "results.json"))
    if not out_file.is_absolute():
        out_file = HERE / out_file
    pool_file = Path(args.pool or env_path("POOL_FILE", "proxiers_google.txt"))
    if not pool_file.is_absolute():
        pool_file = HERE / pool_file

    concurrency = args.concurrency or env_int("CONCURRENCY", 10)
    timeout = args.timeout or float(env_path("REQUEST_TIMEOUT", "15"))
    start_timeout = args.start_timeout or float(env_path("START_TIMEOUT", "12"))
    kernel_pref = args.kernel or env_path("KERNEL", "auto")
    do_ag = (not args.no_antigravity) and env_bool("CHECK_ANTIGRAVITY", True)
    only_usable = args.only_usable or env_bool("ONLY_USABLE", False)

    # ---- 读取节点
    if not in_file.is_file():
        # 首次使用最常见的情况: 还没有节点文件.
        # 不要只报错 —— 直接给出两条可走的路, 并顺手建一个空白文件.
        print(f"[提示] 还没有节点文件: {in_file.name}")
        try:
            in_file.write_text(
                "# 一行一个代理 URI, 例如:\n"
                "# vless://uuid@example.com:443?security=tls&sni=example.com#节点名\n"
                "# hysteria2://password@example.com:443?sni=example.com#节点名\n",
                encoding="utf-8",
            )
            print(f"       已创建空白模板: {in_file}")
        except OSError:
            pass
        print()
        print("  推荐做法 (不用手写文件):")
        print(f"     {Path(sys.executable).name} web\\server.py")
        print("     然后浏览器打开提示的地址, 点「导入节点」粘贴链接即可.")
        print()
        print("  或者: 把节点写进上面的文件, 或指定别的文件: main.py -i 你的文件.txt")
        return 2

    text = in_file.read_text(encoding="utf-8-sig", errors="replace")
    good, bad = parse_many(text)
    if args.limit:
        good = good[: args.limit]

    print(f"读取 {in_file.name}: 可用节点 {len(good)} 个", end="")
    if bad:
        print(f", 解析失败 {len(bad)} 行", end="")
    print()

    if bad:
        for n in bad[:8]:
            print(f"   [跳过] {n.error[:56]}  <- {n.raw[:60]}")
        if len(bad) > 8:
            print(f"   ... 另有 {len(bad) - 8} 行解析失败")
        print()

    if not good:
        # 给出可执行的下一步, 而不是只说"没有节点"
        print(f"[提示] {in_file.name} 里没有可测试的节点"
              + (f" (共 {len(bad)} 行无法解析)" if bad else " (文件是空的或只有注释)"))
        print()
        print("  推荐做法 (不用手写文件):")
        print(f"     {Path(sys.executable).name} web\\server.py")
        print("     浏览器打开后点「导入节点」, 粘贴代理链接即可 (会自动去重).")
        print()
        print("  或: 检查链接是否为完整的 URI (需以 vless:// vmess:// trojan://")
        print("      ss:// hysteria2:// tuic:// 开头).")
        return 2

    # ---- 定位内核
    search_root = env_path("V2RAYN_BIN", r"C:\software\v2rayN-windows-64\bin")
    singbox = find_kernel("sing-box", search_root)
    xray = find_kernel("xray", search_root)

    if not singbox and not xray:
        print("[错误] 未找到 sing-box.exe / xray.exe", file=sys.stderr)
        print(f"       已搜索: {search_root}", file=sys.stderr)
        print("       可在 .env 中设置 SING_BOX_PATH / XRAY_PATH 指定路径,", file=sys.stderr)
        print("       或把可执行文件放到 kernels/ 目录下.", file=sys.stderr)
        return 3

    # ---- 启动编排
    launcher = Launcher(
        workdir=HERE,
        singbox=singbox,
        xray=xray,
        kernel_preference=kernel_pref,
    )
    detector = Detector(timeout=timeout, do_antigravity=do_ag,
                        rtt_rounds=env_int("RTT_ROUNDS", 3))
    from core.tester import Tester

    reporter = Reporter(verbose=not args.quiet)
    reporter.header(len(good), concurrency, launcher.available_kernels())

    total = len(good)
    counter = {"n": 0}
    lock = threading.Lock()

    def on_event(event: str, payload: dict) -> None:
        if event == "result":
            with lock:
                counter["n"] += 1
                idx = counter["n"]
            r = NodeResult(**payload)
            reporter.result(r, idx, total)

    tester = Tester(
        launcher=launcher,
        detector=detector,
        concurrency=concurrency,
        start_timeout=start_timeout,
        on_event=on_event,
        categories=chosen,
    )

    t0 = time.time()
    try:
        results = tester.run(good)
    except KeyboardInterrupt:
        print("\n[中断] 正在回收子进程...", file=sys.stderr)
        launcher.shutdown_all()
        return 130
    finally:
        # 关键: 保证不留孤儿进程
        launcher.shutdown_all()

    elapsed = time.time() - t0
    summary = summarize(results)
    reporter.footer(summary, elapsed)

    # ---- 输出
    # 单个分类时, 结果与代理池按分类分文件, 避免覆盖别的分类
    suffix = chosen[0] if len(chosen) == 1 else ""
    n_pool = write_outputs(results, out_file, pool_file, only_usable, suffix)
    out_name = out_file.with_name(f"results_{suffix}.json").name if suffix else out_file.name
    pool_name = pool_file.with_name(f"proxiers_{suffix}.txt").name if suffix else pool_file.name
    print(f"  结果明细      : {out_name}  ({len(results)} 条)")
    print(f"  可用代理池    : {pool_name}  ({n_pool} 条)")
    if args.clean_configs:
        launcher.cleanup_configs(keep=False)
        print("  临时配置      : 已清理")
    else:
        print(f"  临时配置      : {launcher.cfg_dir} (--clean-configs 可清理)")
    print()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
