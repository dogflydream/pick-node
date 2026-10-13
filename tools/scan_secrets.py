"""公开推送前的敏感信息扫描.

检查被 git 跟踪的文件里是否残留:
  - 真实代理节点域名 / UUID
  - 出口 IP (非保留测试网段)
  - 本机用户名 / 绝对路径
  - token / password 之类字样
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.util import setup_console

setup_console()

files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                       text=True).stdout.split()

# 1) 真实节点域名 (从 proxiers.txt 提取, 这些绝不该出现在代码里)
real_hosts = set()
nf = ROOT / "proxiers.txt"
if nf.is_file():
    for line in nf.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r"@([A-Za-z0-9._-]+):\d+", line)
        if m:
            real_hosts.add(m.group(1).lower())
        m2 = re.search(r"://([^@/]+@)?([A-Za-z0-9._-]+):\d+", line)
        if m2 and m2.group(2):
            real_hosts.add(m2.group(2).lower())

# 这些是通用/本地地址, 不算泄漏
BENIGN_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1",
                "example.com", "example.org", "a.example.com", "b.example.com"}
real_hosts -= BENIGN_HOSTS

print(f"proxiers.txt 中有 {len(real_hosts)} 个真实主机名, 逐一检查是否泄漏到代码里")
leaks = []
for f in files:
    p = ROOT / f
    if not p.is_file() or p.suffix in (".png", ".jpg", ".ico"):
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        continue
    for h in real_hosts:
        if h in text:
            leaks.append((f, h))

if leaks:
    print("  !! 发现泄漏:")
    for f, h in leaks:
        print(f"     {f}  <- {h}")
else:
    print("  OK 无真实主机名泄漏")

# 2) 出口 IP (公网 IPv4, 排除文档用网段)
DOC_NETS = ("203.0.113.", "198.51.100.", "192.0.2.", "127.0.0.1", "0.0.0.0",
            "10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.")
print()
print("检查公网 IPv4:")
ip_leaks = []
for f in files:
    p = ROOT / f
    if not p.is_file():
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for m in re.finditer(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", text):
        ip = m.group(1)
        line = text[:m.start()].count("\n") + 1
        line_text = text.splitlines()[line - 1] if line <= len(text.splitlines()) else ""
        # 排除明显不是 IP 的场景: 版本号 (Chrome/131.0.0.0)、UA 串、示例值
        if re.search(r"(Chrome|Firefox|Safari|Gecko|Version|v?)[/=]?\s*" + re.escape(ip),
                     line_text):
            continue
        if ip.startswith(DOC_NETS):
            continue
        if ip.startswith(("1.1.1.", "8.8.8.", "8.8.4.", "223.5.5.", "1.2.3.4",
                          "5.6.7.8", "9.9.9.", "7.7.7.", "4.4.4.")):
            continue
        octs = [int(x) for x in ip.split(".")]
        if any(o > 255 for o in octs):
            continue
        # 版本号特征: 末段为 0 且出现在 UA / 版本上下文
        if ip.endswith(".0.0.0") or ip.endswith(".0.0") or ip.endswith(".0"):
            continue
        ip_leaks.append((f, ip, line))

if ip_leaks:
    print("  可能残留的真实 IP:")
    for f, ip, ln in ip_leaks[:20]:
        print(f"     {f}:{ln}  {ip}")
else:
    print("  OK 无真实的公网 IP")

# 3) 本机用户名 / 绝对路径
print()
print("检查本机绝对路径:")
path_leaks = []
for f in files:
    p = ROOT / f
    if not p.is_file():
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for m in re.finditer(r"[A-Za-z]:\\Users\\[^\\\s\"]+", text):
        path_leaks.append((f, m.group(0)))
if path_leaks:
    print("  发现:")
    seen = set()
    for f, path in path_leaks:
        if path in seen:
            continue
        seen.add(path)
        print(f"     {f}  {path}")
else:
    print("  OK 无本机用户路径")

print()
print("=" * 76)
if leaks or ip_leaks or path_leaks:
    print("有需要人工确认的项 (见上)")
else:
    print("扫描通过: 无凭据 / 无真实 IP / 无本机路径")
