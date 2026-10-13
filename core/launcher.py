"""代理实例启动器.

职责:
  - 端口分配 (避开系统占用端口, 支持并发安全分配)
  - 为每个节点生成临时配置并拉起独立内核进程
  - 等待内核真正开始监听 (TCP 探测), 这是最关键的一步
  - 进程生命周期管理, 保证异常/中断时全部回收

关于 "内核启动成功" 的判定:
  sing-box/xray 启动到真正 listen 之间有几十~几百毫秒延迟, 也可能因为
  配置语义错误(如 UUID 非法) 或端口冲突而秒退. 因此必须:
    1) 轮询探测端口连通性
    2) 同时检查进程是否已退出 -> 立即读取日志报错, 避免无谓等待
"""

from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config_gen
from .parser import ParsedNode
from .util import find_kernel, popen_kwargs, write_text_nobom


# ---------------------------------------------------------------- 端口分配


class PortAllocator:
    """线程安全的本地端口分配器.

    先探测端口是否真的可绑定, 避免分配到已被占用的端口;
    采用递增游标而非随机, 便于日志排查.
    """

    def __init__(self, start: int = 20001, end: int = 29999):
        self._lock = threading.Lock()
        self._next = start
        self._end = end
        self._handed_out: set[int] = set()

    @staticmethod
    def _bindable(port: int) -> bool:
        """检查 127.0.0.1:port 是否可绑定."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    def acquire(self) -> int:
        with self._lock:
            for _ in range(self._end - self._next + 1):
                p = self._next
                self._next += 1
                if self._next > self._end:
                    self._next = 1 if self._end < 1024 else 20001
                if p in self._handed_out:
                    continue
                if self._bindable(p):
                    self._handed_out.add(p)
                    return p
            raise RuntimeError("没有可用的本地端口")

    def release(self, port: int) -> None:
        with self._lock:
            self._handed_out.discard(port)


# ---------------------------------------------------------------- 实例


@dataclass
class Instance:
    """一个运行中的代理实例."""

    node: ParsedNode
    kernel: str
    port: int
    config_path: Path
    log_path: Path
    proc: subprocess.Popen | None = None
    started_at: float = 0.0
    start_ok: bool = False
    error: str = ""
    stdout_lines: list[str] = field(default_factory=list)

    @property
    def proxy_url_socks(self) -> str:
        return f"socks5://127.0.0.1:{self.port}"

    @property
    def proxy_url_http(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def read_log(self, limit: int = 4000) -> str:
        try:
            data = self.log_path.read_text(encoding="utf-8", errors="replace")
            return data[-limit:]
        except OSError:
            return ""


class Launcher:
    """管理一批代理实例的启动与回收."""

    def __init__(
        self,
        workdir: Path,
        singbox: Path | None = None,
        xray: Path | None = None,
        kernel_preference: str = "auto",
        log_level: str = "error",
    ):
        self.workdir = Path(workdir)
        self.cfg_dir = self.workdir / "configs"
        self.log_dir = self.workdir / "configs" / "logs"
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.singbox = singbox
        self.xray = xray
        self.kernel_preference = kernel_preference
        self.log_level = log_level

        self.ports = PortAllocator()
        self.instances: list[Instance] = []
        self._lock = threading.Lock()
        # 无论正常结束还是异常退出, 都要回收子进程
        atexit.register(self.shutdown_all)

    # ---------------------------------------------------------- 内核可用性

    def available_kernels(self) -> dict[str, Path | None]:
        return {"sing-box": self.singbox, "xray": self.xray}

    def resolve_kernel(self, node: ParsedNode) -> tuple[str, Path]:
        """决定节点用哪个内核, 并检查该内核是否真的存在."""
        name = config_gen.pick_kernel(node, self.kernel_preference)
        exe = self.singbox if name == "sing-box" else self.xray
        if exe is None:
            # 首选内核缺失时, 尝试另一个能支持该协议的内核
            alt = "xray" if name == "sing-box" else "sing-box"
            alt_exe = self.xray if alt == "xray" else self.singbox
            supported = (
                config_gen.XRAY_PROTOCOLS if alt == "xray" else config_gen.SINGBOX_PROTOCOLS
            )
            if alt_exe is not None and node.protocol in supported:
                return alt, alt_exe
            raise RuntimeError(f"内核 {name}.exe 未找到, 且无替代内核可支持 {node.protocol}")
        return name, exe

    # ---------------------------------------------------------- 启动

    def start(self, node: ParsedNode, wait_timeout: float = 12.0) -> Instance:
        """为单个节点启动一个代理实例."""
        kernel, exe = self.resolve_kernel(node)
        port = self.ports.acquire()

        safe_id = node.node_id
        cfg_path = self.cfg_dir / f"{safe_id}.{kernel}.json"
        log_path = self.log_dir / f"{safe_id}.{kernel}.log"

        inst = Instance(node=node, kernel=kernel, port=port,
                        config_path=cfg_path, log_path=log_path)

        try:
            cfg = config_gen.build_config(node, kernel, port, self.log_level)
        except Exception as e:
            inst.error = f"生成配置失败: {type(e).__name__}: {e}"
            self.ports.release(port)
            return inst

        # 让内核把日志写到文件, 便于失败后诊断
        if kernel == "sing-box":
            cfg["log"]["output"] = str(log_path)
        else:
            cfg["log"]["error"] = str(log_path)

        write_text_nobom(cfg_path, config_gen.dumps(cfg))

        # ---- 先做一次配置预检, 快速失败, 避免等待超时
        pre = self._precheck(kernel, exe, cfg_path)
        if pre is not None:
            inst.error = f"配置预检失败: {pre}"
            self.ports.release(port)
            return inst

        # ---- 拉起进程
        if kernel == "sing-box":
            cmd = [str(exe), "run", "-c", str(cfg_path)]
        else:
            cmd = [str(exe), "run", "-c", str(cfg_path)]

        try:
            proc = subprocess.Popen(cmd, cwd=str(self.workdir), **popen_kwargs())
        except OSError as e:
            inst.error = f"启动进程失败: {e}"
            self.ports.release(port)
            return inst

        inst.proc = proc
        inst.started_at = time.time()

        # ---- 等待端口真正监听
        ok, err = self._wait_listening(inst, wait_timeout)
        if ok:
            inst.start_ok = True
        else:
            inst.error = err
            self.stop(inst)

        with self._lock:
            self.instances.append(inst)
        return inst

    def _precheck(self, kernel: str, exe: Path, cfg_path: Path) -> str | None:
        """用内核自带的 check/-test 做配置预检, 返回错误文本或 None."""
        if kernel == "sing-box":
            cmd = [str(exe), "check", "-c", str(cfg_path)]
        else:
            cmd = [str(exe), "run", "-test", "-c", str(cfg_path)]
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=25,
                creationflags=popen_kwargs().get("creationflags", 0),
            )
        except subprocess.TimeoutExpired:
            return "预检超时"
        except OSError as e:
            return f"无法执行预检: {e}"
        if r.returncode == 0:
            return None
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        # 过滤掉 banner 噪声, 只留关键行
        lines = [ln for ln in out.splitlines()
                 if ln.strip() and "Penetrates Everything" not in ln
                 and "anti-censorship" not in ln
                 and not ln.startswith("Xray ")
                 and "Reading config" not in ln]
        return " | ".join(lines)[:300] or f"返回码 {r.returncode}"

    def _wait_listening(self, inst: Instance, timeout: float) -> tuple[bool, str]:
        """轮询等待端口可连接; 期间若进程退出则立即返回错误."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            # 进程已退出 -> 立刻读取日志报错, 不再空等
            if inst.proc is not None and inst.proc.poll() is not None:
                rc = inst.proc.returncode
                log = inst.read_log(1200).strip().replace("\n", " ")
                return False, f"内核启动即退出 (code={rc}) {log[:260]}"

            if self._port_open("127.0.0.1", inst.port):
                return True, ""
            time.sleep(0.08)
        return False, f"等待端口 {inst.port} 监听超时 ({timeout:.0f}s)"

    @staticmethod
    def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            try:
                return s.connect_ex((host, port)) == 0
            except OSError:
                return False

    # ---------------------------------------------------------- 回收

    def stop(self, inst: Instance) -> None:
        """终止实例进程并释放端口."""
        proc = inst.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
            except OSError:
                pass
        inst.proc = None
        try:
            self.ports.release(inst.port)
        except Exception:
            pass

    def shutdown_all(self) -> None:
        """回收全部实例 (幂等, 可被 atexit 重复调用)."""
        with self._lock:
            current = list(self.instances)
            self.instances.clear()
        for inst in current:
            self.stop(inst)

    def cleanup_configs(self, keep: bool = True) -> None:
        """清理临时配置. keep=True 时保留, 便于排查."""
        if keep:
            return
        for p in self.cfg_dir.glob("*.json"):
            try:
                p.unlink()
            except OSError:
                pass
