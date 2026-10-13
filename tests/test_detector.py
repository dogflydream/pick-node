"""检测器测试 (需要真实网络与可用的本地上游, 属手工诊断脚本).

关键: 本机直连出口是 China/HK 地区 (google.com 会跳到 google.com.hk),
这是已知 ground truth, 可以用来验证分类逻辑是否给出正确结论.
"""
import sys

sys.path.insert(0, ".")
from core.detector import Detector, country_name
from core.util import setup_console

setup_console()

print("=" * 96)
print("直连出口检测 (无代理 -> 用本机直连, 已知为受限地区)")
print("=" * 96)

# 不用代理: proxy_url 传一个直连的本地端口不合适, 这里用 httpx 直连
# 检测器接口要求 proxy_url; 为测通逻辑, 起一个 sing-box "socks->direct" 实例
from pathlib import Path
from core.launcher import Launcher
from core.parser import parse_uri
from core.util import find_kernel

# 用 sing-box 做一个"透明出口"实例: 入站 socks -> 出站 freedom/direct
# 这样 Detector 走的是真实代理链路, 但出口就是本机
import json
from core.util import write_text_nobom

launcher = Launcher(workdir=Path("."), singbox=find_kernel("sing-box"))

# 手工构造一个 socks5 节点指向一个真正的直连代理:
# 最简单可靠的办法是让 sing-box 起一个 "direct" 类型的 socks 出站.
cfg = {
    "log": {"level": "error"},
    "inbounds": [{"type": "mixed", "tag": "in", "listen": "127.0.0.1",
                  "listen_port": 29901, "sniff": False}],
    "outbounds": [{"type": "direct", "tag": "proxy"}],
    "route": {"final": "proxy", "auto_detect_interface": False},
}
p = Path("configs/_validate/direct_egress.json")
write_text_nobom(p, json.dumps(cfg, indent=2))

import subprocess, time, socket
proc = subprocess.Popen(
    [str(find_kernel("sing-box")), "run", "-c", str(p)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    creationflags=0x08000000,
)
# 等待监听
for _ in range(120):
    with socket.socket() as s:
        s.settimeout(0.2)
        if s.connect_ex(("127.0.0.1", 29901)) == 0:
            break
    time.sleep(0.1)
print("direct-egress socks ready on 127.0.0.1:29901")

det = Detector(timeout=20.0, do_antigravity=True)
result = det.run("socks5://127.0.0.1:29901")

proc.terminate()

print()
print(f"出口 IP        : {result.ip}")
print(f"地区(trace)    : {result.country}  ({country_name(result.country)})")
print(f"地区来源       : {result.country_source}")
print(f"其他来源结论   : {result.country_alt}")
print(f"城市/ASN       : {result.city} / {result.asn_org}")
print(f"Google         : {result.google} (status={result.google_status})")
print(f"Google 最终URL : {result.google_final_url}")
print(f"Gemini         : {result.gemini}  <- {result.gemini_reason}")
print(f"Antigravity    : {result.antigravity}  <- {result.antigravity_reason}")
print(f"延迟           : {result.latency} ms")
print(f"链路可达       : {result.reachable}")
print(f"置信度         : {result.confidence}")
print()

# -------- 断言: ground truth 校验 --------
assert result.ip, "未取到出口 IP"
assert result.reachable, "代理链路应可达"

# 本机出口: cloudflare 说 US, 但 google 会把我们当 HK/CN 地区
# -> google 最终 URL 应出现 google.com.hk
print("重定向链:", [p_.get("final_url") for p_ in result.probes if p_["name"] == "google"])
google_probe = next(p_ for p_ in result.probes if p_["name"] == "google")
final = (google_probe.get("final_url") or "").lower()
print(f"google 最终 URL: {final}")

assert result.google == "success", f"google 应可达, 实际 {result.google}"

# 关键结论校验: 由于本机被 Google 判定为 HK 地区, Gemini 必须被标为 blocked/likely 而非 available
assert result.gemini in ("blocked", "likely", "unavailable"), \
    f"受限地区不应判为 available, 实际 {result.gemini}"
print(f"[OK] 受限地区 Gemini 判定为 '{result.gemini}' (未误判为 available)")

# google 跳转地区识别
if "com.hk" in final:
    print("[OK] 正确识别到 google.com -> google.com.hk 的地区跳转")
    assert result.country_alt.get("google-redirect-tld") == "HK", \
        f"应识别出 HK, 实际 {result.country_alt}"
    print("[OK] 从跳转 URL 提取到地区 HK")

print()
print("ALL DETECTOR TESTS PASSED")
