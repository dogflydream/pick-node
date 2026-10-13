"""解析器自测: 覆盖全部协议 + 异常输入."""
import base64
import json
import sys

sys.path.insert(0, ".")
from core.parser import parse_many, parse_uri
from core.util import setup_console

setup_console()


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


vmess_obj = {
    "v": "2", "ps": "VMess-WS-TLS", "add": "vm.example.com", "port": "443",
    "id": "b831381d-6324-4d53-ad4f-8cda48b30811", "aid": "0", "scy": "auto",
    "net": "ws", "type": "none", "host": "vm.example.com", "path": "/ray",
    "tls": "tls", "sni": "vm.example.com",
}

CASES = [
    # VLESS + Reality + Vision
    "vless://11111111-2222-3333-4444-555555555555@1.2.3.4:443"
    "?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome"
    "&pbk=AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIIIJJJJKKK&sid=0123abcd"
    "&type=tcp&flow=xtls-rprx-vision#Reality-JP",
    # VLESS + WS + TLS
    "vless://11111111-2222-3333-4444-555555555555@ws.example.com:443"
    "?encryption=none&security=tls&sni=ws.example.com&type=ws&path=%2Fws&host=ws.example.com#WS-Node",
    # VLESS + gRPC
    "vless://11111111-2222-3333-4444-555555555555@grpc.example.com:443"
    "?encryption=none&security=tls&type=grpc&serviceName=grpcsvc&sni=grpc.example.com#gRPC-Node",
    # VMess
    "vmess://" + b64(json.dumps(vmess_obj)),
    # Trojan
    "trojan://mypassword@tj.example.com:443?sni=tj.example.com&type=tcp#Trojan-SG",
    # Trojan + WS
    "trojan://pw@tj2.example.com:443?type=ws&path=%2Ftj&host=tj2.example.com#Trojan-WS",
    # SS SIP002
    "ss://" + b64("aes-256-gcm:mypassword") + "@ss.example.com:8388#SS-SIP002",
    # SS 旧式整段 base64
    "ss://" + b64("aes-128-gcm:pw123@ss2.example.com:8388") + "#SS-Legacy",
    # SS 2022
    "ss://" + b64("2022-blake3-aes-128-gcm:AAAAAAAAAAAAAAAAAAAAAA==") + "@ss3.example.com:8388#SS2022",
    # Hysteria2
    "hysteria2://hy2password@hy.example.com:443?sni=hy.example.com&insecure=0#HY2-US",
    # hy2 别名 + obfs
    "hy2://pw@hy2.example.com:8443?sni=hy2.example.com&obfs=salamander&obfs-password=secret#HY2-Obfs",
    # TUIC
    "tuic://11111111-2222-3333-4444-555555555555:tuicpw@tuic.example.com:443"
    "?sni=tuic.example.com&congestion_control=bbr&alpn=h3#TUIC-DE",
    # IPv6
    "vless://11111111-2222-3333-4444-555555555555@[2001:db8::1]:443?security=tls&sni=v6.example.com#IPv6-Node",
    # ---- 以下是应当失败的 ----
    "ssr://whatever",
    "not-a-uri",
    "vless://@1.2.3.4:443",
    "vmess://" + b64("this is not json"),
    "trojan://pw@bad.example.com:99999",
    "",
    "# comment line",
]

good, bad = parse_many("\n".join(CASES))

print("=" * 100)
print(f"OK nodes: {len(good)}   FAILED: {len(bad)}")
print("=" * 100)
for n in good:
    print(f"[{n.protocol:9}] id={n.node_id} {n.server}:{n.port} net={n.network:11} "
          f"tls={int(n.tls)} sni={n.sni[:26]:26} flow={n.flow or '-':18} tag={n.tag[:22]}")
print()
print("-" * 100)
for n in bad:
    print(f"[REJECT] {n.error[:52]:54} <- {n.raw[:52]}")

# 断言关键字段
by_tag = {n.tag: n for n in good}
assert by_tag["Reality-JP"].reality_pubkey.startswith("AAAA"), "reality pbk"
assert by_tag["Reality-JP"].flow == "xtls-rprx-vision", "vision flow"
assert by_tag["WS-Node"].path == "/ws", "ws path unquote"
assert by_tag["gRPC-Node"].service_name == "grpcsvc", "grpc serviceName"
assert by_tag["VMess-WS-TLS"].network == "ws" and by_tag["VMess-WS-TLS"].tls, "vmess"
assert by_tag["SS-SIP002"].method == "aes-256-gcm", "ss sip002 method"
assert by_tag["SS-Legacy"].password == "pw123", "ss legacy password"
assert by_tag["SS-Legacy"].server == "ss2.example.com", "ss legacy host"
assert by_tag["HY2-Obfs"].obfs == "salamander", "hy2 obfs"
assert by_tag["TUIC-DE"].congestion == "bbr", "tuic congestion"
assert by_tag["IPv6-Node"].server == "2001:db8::1", "ipv6 parse"
assert len(bad) == 5, f"expected 5 rejects, got {len(bad)}"

# 去重验证
dup_good, _ = parse_many("\n".join([CASES[0], CASES[0], CASES[0]]))
assert len(dup_good) == 1, f"dedup failed: {len(dup_good)}"

print()
print("ALL PARSER ASSERTIONS PASSED")
