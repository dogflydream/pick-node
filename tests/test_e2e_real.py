"""端到端: 用真实节点验证 启动 -> 检测 全链路.

重点验证:
  1. 实例能起来
  2. 出口 IP 与 v2rayN 系统代理(3067)的出口不同 -> 证明没有被系统代理劫持
  3. 地区/Google/Gemini 结论合理
"""
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
import httpx

from core.detector import Detector, country_name
from core.launcher import Launcher
from core.parser import load_nodes_file
from core.util import find_kernel, setup_console

setup_console()

# --- 参照物: v2rayN 系统代理的出口 (若节点检测结果与它相同, 说明被劫持) ---
ref_ip = None
try:
    r = httpx.get("https://www.cloudflare.com/cdn-cgi/trace", timeout=15)
    for line in r.text.splitlines():
        if line.startswith("ip="):
            ref_ip = line.split("=", 1)[1].strip()
except Exception as e:
    print("v2rayN 出口获取失败:", type(e).__name__)
print(f"参照出口 (v2rayN 3087/3067 系统代理): {ref_ip}")
print()

good, bad = load_nodes_file("proxiers.txt")
print(f"载入节点: OK={len(good)} FAILED={len(bad)}")

launcher = Launcher(workdir=Path("."), singbox=find_kernel("sing-box"),
                    xray=find_kernel("xray"))
detector = Detector(timeout=15.0, do_antigravity=True)

# 取前若干个不同协议的节点做抽样
sample = []
seen_proto = set()
for n in good:
    if n.protocol not in seen_proto:
        seen_proto.add(n.protocol)
        sample.append(n)
sample += [n for n in good if n not in sample][:3]

print(f"抽样 {len(sample)} 个节点")
print("=" * 104)

results = []
for i, node in enumerate(sample, 1):
    tag = node.tag[:34]
    print(f"\n[{i}/{len(sample)}] {node.protocol.upper()} {node.server}:{node.port}  {tag}")
    inst = launcher.start(node, wait_timeout=10)
    if not inst.start_ok:
        print(f"   [启动失败] {inst.error[:150]}")
        results.append((node, None, inst.error))
        continue
    print(f"   实例就绪 -> 127.0.0.1:{inst.port} ({inst.kernel})")

    det = detector.run(f"socks5://127.0.0.1:{inst.port}")
    print(f"   出口 IP   : {det.ip or '(未取到)'}")
    print(f"   地区      : {det.country} ({country_name(det.country)})  src={det.country_source}")
    print(f"   Google    : {det.google} (status={det.google_status}) -> {det.google_final_url[:60]}")
    print(f"   Gemini    : {det.gemini}  ({det.gemini_reason[:70]})")
    print(f"   Antigrav  : {det.antigravity}  ({det.antigravity_reason[:60]})")
    print(f"   延迟      : {det.latency} ms")
    if det.errors:
        print(f"   错误      : {det.errors[:2]}")

    # 关键校验: 不能被系统代理劫持
    if ref_ip and det.ip:
        if det.ip == ref_ip:
            print(f"   [!!] 出口 IP 与系统代理相同 ({ref_ip}) —— 可能被劫持")
        else:
            print("   [OK] 出口 IP 与系统代理不同, 代理链路独立生效")

    results.append((node, det, None))
    launcher.stop(inst)

launcher.shutdown_all()
print()
print("=" * 104)
ok_cnt = sum(1 for _, d, e in results if d and d.reachable)
print(f"完成: {len(results)} 个节点, 链路可达 {ok_cnt}")
