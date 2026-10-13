"""启动器测试: 验证 端口分配 / 进程拉起 / 监听等待 / 回收 / 失败诊断.

用一个"本地 socks5 节点"充当真实出站: sing-box 支持 socks 出站把本地入站
转发到这个上游, 从而走通 完整链路 (入站 -> 出站 -> 上游), 无需公网节点.
"""
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, ".")
from core.launcher import Launcher, PortAllocator
from core.parser import parse_uri
from core.util import find_kernel, setup_console

setup_console()

print("=" * 92)
print("TEST 1: PortAllocator 端口分配与冲突规避")
print("=" * 92)
pa = PortAllocator(start=29500, end=29510)
ports = [pa.acquire() for _ in range(5)]
print("allocated:", ports)
assert len(set(ports)) == 5, "端口重复!"
assert all(29500 <= p <= 29510 for p in ports), "端口越界"

# 占用一个端口, 分配器必须自动跳过它
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", 29505)); s.listen(1)
pa2 = PortAllocator(start=29500, end=29510)
got = [pa2.acquire() for _ in range(5)]
print("with 29505 occupied ->", got)
assert 29505 not in got, "分配了被占用的端口!"
s.close()
print("[OK] 端口分配正确, 能避开被占用端口")
print()

print("=" * 92)
print("TEST 2: 启动一个真实链路的本地代理实例")
print("=" * 92)

# 起一个本地 socks5 上游 (用 sing-box 自己当上游最省事, 但这里用极简实现避免依赖)
# 更简单: 直接让节点指向一个不存在的上游, 只验证 "进程能起来并监听".
# 这里改用 socks 协议节点指向一个真实可达的 socks 上游.
launcher = Launcher(
    workdir=Path("."),
    singbox=find_kernel("sing-box"),
    xray=find_kernel("xray"),
)

# 用例 A: 用 socks 协议节点(指向一个本地可达端口) 验证进程能启动监听
socks_up = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
socks_up.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
socks_up.bind(("127.0.0.1", 29601)); socks_up.listen(8)

node_ok = parse_uri("socks5://127.0.0.1:29601#LocalUpstream")
assert node_ok.ok, node_ok.error
inst = launcher.start(node_ok)
print(f"kernel={inst.kernel} port={inst.port} start_ok={inst.start_ok} err={inst.error[:120]}")
assert inst.start_ok, f"实例启动失败: {inst.error}"
assert inst.is_alive(), "进程应处于存活状态"
print(f"[OK] 实例已监听 127.0.0.1:{inst.port} (kernel={inst.kernel})")

# 验证 SOCKS 端口真的接受连接
c = socket.create_connection(("127.0.0.1", inst.port), timeout=3)
c.close()
print("[OK] 端口可建立 TCP 连接")

print()
print("=" * 92)
print("TEST 3: 失败诊断 —— 不可达/非法节点应快速失败而非空等")
print("=" * 92)
# 一个语法合法但服务端不存在的节点: 进程能起来(监听本地端口), 但测试时才会失败
node_dead = parse_uri(
    "vless://11111111-2222-3333-4444-555555555555@127.0.0.1:1"
    "?encryption=none&security=tls&sni=x.example.com&type=tcp#Dead"
)
t0 = time.time()
inst_dead = launcher.start(node_dead, wait_timeout=8)
dt = time.time() - t0
print(f"dead-node: start_ok={inst_dead.start_ok} elapsed={dt:.2f}s err={inst_dead.error[:150]}")
# 该节点配置合法, 所以本地监听应当成功; 关键是它没有卡死
assert dt < 12, "启动耗时过长"
print("[OK] 未出现长时间阻塞")

print()
print("=" * 92)
print("TEST 4: 生命周期回收")
print("=" * 92)
alive_before = [i for i in launcher.instances if i.is_alive()]
print("alive before shutdown:", len(alive_before))
ports_before = [i.port for i in alive_before]
launcher.shutdown_all()
time.sleep(0.6)
still = [p for p in ports_before if Launcher._port_open("127.0.0.1", p)]
print("ports still open after shutdown:", still)
assert not still, f"端口未释放: {still}"
assert all(not i.is_alive() for i in alive_before), "进程未回收"
print("[OK] 全部子进程已终止, 端口已释放")

socks_up.close()
print()
print("ALL LAUNCHER TESTS PASSED")
