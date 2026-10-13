"""配置生成校验: 对每个协议用例生成配置, 并用真实内核做 check.

sing-box: check -c <file>
xray:     run -test -c <file>
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, ".")
from core.config_gen import build_config, dumps, pick_kernel
from core.parser import parse_uri
from core.util import find_kernel, setup_console, write_text_nobom

setup_console()

SB = find_kernel("sing-box")
XR = find_kernel("xray")
OUT = Path("configs/_validate")
OUT.mkdir(parents=True, exist_ok=True)

URIS = {
    "vless-reality": "vless://11111111-2222-3333-4444-555555555555@1.2.3.4:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIIIJJJJKKK&sid=0123abcd&type=tcp&flow=xtls-rprx-vision#r",
    "vless-ws-tls": "vless://11111111-2222-3333-4444-555555555555@a.example.com:443?encryption=none&security=tls&sni=a.example.com&type=ws&path=%2Fws&host=a.example.com#w",
    "vless-grpc": "vless://11111111-2222-3333-4444-555555555555@g.example.com:443?encryption=none&security=tls&type=grpc&serviceName=gsvc&sni=g.example.com#g",
    "vless-tcp-tls": "vless://11111111-2222-3333-4444-555555555555@t.example.com:443?encryption=none&security=tls&sni=t.example.com#t",
    "vmess-ws-tls": "vmess://" + __import__("base64").b64encode(json.dumps({
        "v": "2", "ps": "v", "add": "vm.example.com", "port": "443",
        "id": "b831381d-6324-4d53-ad4f-8cda48b30811", "aid": "0", "scy": "auto",
        "net": "ws", "type": "none", "host": "vm.example.com", "path": "/ray",
        "tls": "tls", "sni": "vm.example.com"}).encode()).decode(),
    "vmess-tcp": "vmess://" + __import__("base64").b64encode(json.dumps({
        "v": "2", "ps": "v2", "add": "vm2.example.com", "port": "8080",
        "id": "b831381d-6324-4d53-ad4f-8cda48b30811", "aid": "0", "scy": "auto",
        "net": "tcp", "type": "none", "tls": ""}).encode()).decode(),
    "trojan-tcp": "trojan://mypassword@tj.example.com:443?sni=tj.example.com&type=tcp#tj",
    "trojan-ws": "trojan://pw@tj2.example.com:443?type=ws&path=%2Ftj&host=tj2.example.com&sni=tj2.example.com#tj2",
    "ss-gcm": "ss://" + __import__("base64").b64encode(b"aes-256-gcm:mypassword").decode() + "@ss.example.com:8388#ss",
    "ss-2022": "ss://" + __import__("base64").b64encode(b"2022-blake3-aes-128-gcm:AAAAAAAAAAAAAAAAAAAAAA==").decode() + "@ss2.example.com:8388#ss2",
    "ss-chacha": "ss://" + __import__("base64").b64encode(b"chacha20-ietf-poly1305:pw").decode() + "@ss3.example.com:8388#ss3",
    "hy2": "hysteria2://hy2password@hy.example.com:443?sni=hy.example.com#hy",
    "hy2-obfs": "hy2://pw@hy2.example.com:8443?sni=hy2.example.com&obfs=salamander&obfs-password=secret#hy2",
    "tuic": "tuic://11111111-2222-3333-4444-555555555555:tuicpw@tuic.example.com:443?sni=tuic.example.com&congestion_control=bbr&alpn=h3#tuic",
    "socks5": "socks5://user:pass@127.0.0.1:1080#socks",
    "http-proxy": "http://user:pass@127.0.0.1:8080#http",
}

port = 21000
results = []
for name, uri in URIS.items():
    node = parse_uri(uri)
    assert node.ok, f"{name}: parse failed {node.error}"

    kernels = ["sing-box"] + (["xray"] if node.protocol in ("vless", "vmess", "trojan", "ss", "socks", "http") else [])
    for kernel in kernels:
        port += 1
        exe = SB if kernel == "sing-box" else XR
        try:
            cfg = build_config(node, kernel, port)
        except Exception as e:
            results.append((name, kernel, False, f"build error: {e}"))
            continue

        p = OUT / f"{name}.{kernel}.json"
        write_text_nobom(p, dumps(cfg))

        if kernel == "sing-box":
            cmd = [str(exe), "check", "-c", str(p)]
        else:
            cmd = [str(exe), "run", "-test", "-c", str(p)]

        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=40,
                           creationflags=0x08000000)
        ok = r.returncode == 0
        msg = ""
        if not ok:
            msg = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " ")[:180]
        results.append((name, kernel, ok, msg))

print("=" * 104)
print(f"{'NODE':16} {'KERNEL':9} RESULT")
print("=" * 104)
nfail = 0
for name, kernel, ok, msg in results:
    if ok:
        print(f"{name:16} {kernel:9} OK")
    else:
        nfail += 1
        print(f"{name:16} {kernel:9} FAIL  {msg}")
print("=" * 104)
print(f"total={len(results)}  failed={nfail}")
if nfail:
    sys.exit(1)
print("ALL GENERATED CONFIGS VALIDATED BY REAL KERNELS")
