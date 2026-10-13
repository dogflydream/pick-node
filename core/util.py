"""公共工具: 控制台编码、子进程无窗口创建、路径探测."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- 控制台编码


def setup_console() -> None:
    """让 Windows 控制台正确输出中文.

    Windows 的 cmd/PowerShell 默认代码页常为 936(GBK) 或 437, 直接 print 中文会乱码.
    这里把标准流强制切到 UTF-8, 并尝试把控制台代码页设为 65001.
    """
    if os.name != "nt":
        return
    try:
        # Python 3.7+: 让 stdout/stderr 使用 utf-8 (errors=replace 避免个别字符崩溃)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass


# ---------------------------------------------------------------- 子进程


def popen_kwargs() -> dict:
    """创建子进程的通用参数.

    关键点: 隐藏内核进程窗口 + 让子进程独立于父进程的信号/控制台,
    这样关闭本工具时不会弹出一堆窗口气息, 也便于统一 kill.
    """
    kw: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        # 0x08000000 = CREATE_NO_WINDOW
        kw["creationflags"] = 0x08000000 | 0x00000008  # NO_WINDOW | DETACHED_PROCESS
    else:
        kw["start_new_session"] = True
    return kw


def write_text_nobom(path: Path, text: str) -> None:
    """写 UTF-8 无 BOM 文件.

    必须无 BOM: sing-box / xray 解析 JSON 时遇到 BOM 会直接 FATAL
    ("invalid character 'ï' looking for beginning of value").
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ---------------------------------------------------------------- 内核探测

# v2rayN 常见的目录布局: bin/<kernel>/<kernel>.exe
_KERNEL_SUBDIRS = {
    "sing-box": ["sing_box", "sing-box", "singbox"],
    "xray": ["xray", "Xray"],
}


def find_kernel(name: str, search_root: str | None = None) -> Path | None:
    """在若干候选位置查找内核可执行文件.

    查找顺序:
      1. 环境变量 PROXY_TESTER_<NAME> / <NAME>_PATH
      2. 项目内 kernels/ 目录
      3. v2rayN 的 bin 目录 (含子目录布局)
      4. 系统 PATH
    """
    exe = f"{name}.exe" if os.name == "nt" else name
    env_keys = [f"PROXY_TESTER_{name.upper().replace('-', '_')}",
                f"{name.upper().replace('-', '_')}_PATH"]
    candidates: list[Path] = []

    for k in env_keys:
        v = os.environ.get(k)
        if v:
            candidates.append(Path(v))

    here = Path(__file__).resolve().parent.parent
    candidates.append(here / "kernels" / exe)
    for sub in _KERNEL_SUBDIRS.get(name, [name]):
        candidates.append(here / "kernels" / sub / exe)

    roots = []
    if search_root:
        roots.append(Path(search_root))
    roots.append(Path(r"C:\software\v2rayN-windows-64\bin"))
    for r in roots:
        candidates.append(r / exe)
        for sub in _KERNEL_SUBDIRS.get(name, [name]):
            candidates.append(r / sub / exe)

    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue

    # 最后尝试 PATH
    from shutil import which

    w = which(exe)
    return Path(w) if w else None


def kernel_version(exe: Path) -> str:
    """读取内核版本字符串, 用于启动时展示与排错."""
    try:
        p = subprocess.run(
            [str(exe), "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            **{k: v for k, v in popen_kwargs().items() if k == "creationflags"},
        )
        out = (p.stdout or "") + (p.stderr or "")
        first = out.strip().splitlines()[0] if out.strip() else "unknown"
        return first.strip()
    except Exception as e:
        return f"<无法读取版本: {type(e).__name__}>"
