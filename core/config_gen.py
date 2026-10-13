"""内核配置生成器.

为每个节点生成独立的内核配置: 一个本地 SOCKS5(+HTTP) 入站 -> 一个出站(节点).

选型策略:
  - sing-box 支持全部目标协议 (vless/vmess/trojan/ss/hysteria2/tuic), 作为首选内核.
  - xray 对 vless/vmess/trojan/ss 支持成熟, 作为这些协议的备选内核.
  - hysteria2 / tuic 仅 sing-box 支持, 自动锁定内核.

生成的配置已针对 sing-box 1.13.x 与 Xray 26.x 的实际 schema 校验过:
  - sing-box 1.13 使用 "type" 字段 (旧版 "protocol" 已废弃), h2 传输已更名为 http.
  - 配置文件必须以 UTF-8 无 BOM 写入, 否则内核会 FATAL.
"""

from __future__ import annotations

import json
from typing import Any

from .parser import ParsedNode

# 各内核支持的协议矩阵
SINGBOX_PROTOCOLS = {"vless", "vmess", "trojan", "ss", "hysteria2", "tuic", "socks", "http"}
XRAY_PROTOCOLS = {"vless", "vmess", "trojan", "ss", "socks", "http"}

# URI 里的简写 -> 内核配置里的正式协议名.
# 实测: sing-box 1.13 与 Xray 26 都要求 "shadowsocks", 写成 "ss" 会报
# "unknown outbound type: ss" / "unknown config id: ss".
_KERNEL_PROTOCOL_NAME = {
    "ss": "shadowsocks",
    "hysteria2": "hysteria2",
    "vless": "vless",
    "vmess": "vmess",
    "trojan": "trojan",
    "tuic": "tuic",
    "socks": "socks",
    "http": "http",
}


def kernel_protocol_name(node: ParsedNode) -> str:
    return _KERNEL_PROTOCOL_NAME.get(node.protocol, node.protocol)


def pick_kernel(node: ParsedNode, preference: str = "auto") -> str:
    """选择用于该节点的内核名 ('sing-box' / 'xray')."""
    if preference == "sing-box":
        return "sing-box"
    if preference == "xray":
        if node.protocol in XRAY_PROTOCOLS:
            return "xray"
        return "sing-box"  # xray 不支持该协议, 回退
    # auto: 优先 sing-box (协议覆盖最全)
    if node.protocol in SINGBOX_PROTOCOLS:
        return "sing-box"
    return "xray"


# ---------------------------------------------------------------- sing-box


def _sb_tls(node: ParsedNode) -> dict[str, Any] | None:
    if not node.tls:
        return None
    tls: dict[str, Any] = {"enabled": True}

    if node.reality_pubkey:
        tls["server_name"] = node.sni or node.server
        tls["utls"] = {"enabled": True, "fingerprint": node.fingerprint or "chrome"}
        tls["reality"] = {
            "enabled": True,
            "public_key": node.reality_pubkey,
            "short_id": node.reality_short_id or "",
        }
        return tls

    if node.sni:
        tls["server_name"] = node.sni
    if node.alpn:
        tls["alpn"] = node.alpn
    if node.allow_insecure:
        tls["insecure"] = True
    if node.fingerprint:
        tls["utls"] = {"enabled": True, "fingerprint": node.fingerprint}
    # 证书指纹固定. 注意: sing-box 1.13 的字段名是 certificate_public_key_sha256
    # (字符串数组); tls.certificate 是"证书文件路径"而非对象, 写错会让内核 panic.
    if node.pin_sha256:
        tls["certificate_public_key_sha256"] = [node.pin_sha256]
    return tls


def _sb_transport(node: ParsedNode) -> dict[str, Any] | None:
    net = (node.network or "tcp").lower()
    if net == "ws":
        t: dict[str, Any] = {"type": "ws", "path": node.path or "/"}
        if node.host:
            t["headers"] = {"Host": node.host}
        return t
    if net in ("http", "h2"):
        t = {"type": "http", "path": node.path or "/"}
        if node.host:
            t["host"] = [h for h in node.host.split(",") if h]
        return t
    if net == "httpupgrade":
        t = {"type": "httpupgrade", "path": node.path or "/"}
        if node.host:
            t["host"] = node.host
        return t
    if net == "grpc":
        return {"type": "grpc", "service_name": node.service_name or ""}
    if net == "quic":
        return {"type": "quic"}
    return None


def build_singbox_config(node: ParsedNode, port: int, log_level: str = "error") -> dict[str, Any]:
    """生成 sing-box 配置 (1.13+ schema)."""
    out: dict[str, Any] = {
        "type": kernel_protocol_name(node),
        "tag": "proxy",
        "server": node.server,
        "server_port": node.port,
    }

    if node.protocol == "vless":
        out["uuid"] = node.uuid
        if node.flow:
            out["flow"] = node.flow
    elif node.protocol == "vmess":
        out["uuid"] = node.uuid
        out["alter_id"] = node.alter_id
        out["security"] = node.security or "auto"
    elif node.protocol == "trojan":
        out["password"] = node.password
    elif node.protocol == "ss":
        out["method"] = node.method
        out["password"] = node.password
    elif node.protocol == "hysteria2":
        out["password"] = node.password
        if node.obfs:
            out["obfs"] = {"type": node.obfs, "password": node.obfs_password}
        # 端口跳跃: "35000-39000" -> ["35000:39000"] (sing-box 用冒号表示范围)
        if node.server_ports:
            rng = node.server_ports.replace("-", ":").split(",")
            out["server_ports"] = [r.strip() for r in rng if r.strip()]
    elif node.protocol == "tuic":
        out["uuid"] = node.uuid
        if node.password:
            out["password"] = node.password
        if node.congestion:
            out["congestion_control"] = node.congestion
    elif node.protocol in ("socks", "http"):
        # 用于链式/本地上游场景. 注意: sing-box 的 socks 出站支持 version 字段,
        # 但 http 出站不支持 (写了会报 unknown field "version").
        if node.protocol == "socks":
            out["version"] = "5"
        if node.uuid:
            out["username"] = node.uuid
        if node.password:
            out["password"] = node.password

    tls = _sb_tls(node)
    if tls:
        out["tls"] = tls

    # hysteria2 / tuic 自带传输语义, 不设 transport
    if node.protocol not in ("hysteria2", "tuic"):
        tr = _sb_transport(node)
        if tr:
            out["transport"] = tr

    return {
        "log": {"level": log_level, "timestamp": True},
        "inbounds": [
            {
                "type": "mixed",
                "tag": "in",
                "listen": "127.0.0.1",
                "listen_port": port,
                "sniff": False,
                "set_system_proxy": False,
            }
        ],
        "outbounds": [out],
        "route": {"rules": [], "final": "proxy", "auto_detect_interface": False},
    }


# ---------------------------------------------------------------- xray


def _xr_stream(node: ParsedNode) -> dict[str, Any]:
    s: dict[str, Any] = {"network": node.network or "tcp"}

    if node.network == "ws":
        s["wsSettings"] = {"path": node.path or "/",
                           "headers": {"Host": node.host} if node.host else {}}
    elif node.network in ("http", "h2"):
        s["network"] = "h2"
        s["httpSettings"] = {
            "path": node.path or "/",
            "host": [h for h in node.host.split(",") if h] if node.host else [],
        }
    elif node.network == "grpc":
        s["grpcSettings"] = {"serviceName": node.service_name or ""}
    elif node.network == "httpupgrade":
        s["httpupgradeSettings"] = {"path": node.path or "/", "host": node.host or ""}
    elif node.network == "quic":
        s["quicSettings"] = {"security": "none", "key": "", "header": {"type": "none"}}

    if not node.tls:
        s["security"] = "none"
        return s

    if node.reality_pubkey:
        s["security"] = "reality"
        s["realitySettings"] = {
            "serverName": node.sni or node.server,
            "fingerprint": node.fingerprint or "chrome",
            "publicKey": node.reality_pubkey,
            "shortId": node.reality_short_id or "",
            "show": False,
        }
    else:
        s["security"] = "tls"
        tls: dict[str, Any] = {}
        if node.sni:
            tls["serverName"] = node.sni
        if node.alpn:
            tls["alpn"] = node.alpn
        if node.fingerprint:
            tls["fingerprint"] = node.fingerprint
        if node.allow_insecure:
            tls["allowInsecure"] = True
        s["tlsSettings"] = tls
    return s


def build_xray_config(node: ParsedNode, port: int, log_level: str = "error") -> dict[str, Any]:
    """生成 Xray 配置 (26.x schema)."""
    if node.protocol == "vless":
        user: dict[str, Any] = {"id": node.uuid, "encryption": "none"}
        if node.flow:
            user["flow"] = node.flow
        settings = {"vnext": [{"address": node.server, "port": node.port, "users": [user]}]}
    elif node.protocol == "vmess":
        settings = {
            "vnext": [
                {
                    "address": node.server,
                    "port": node.port,
                    "users": [
                        {
                            "id": node.uuid,
                            "alterId": node.alter_id,
                            "security": node.security or "auto",
                        }
                    ],
                }
            ]
        }
    elif node.protocol == "trojan":
        settings = {
            "servers": [
                {"address": node.server, "port": node.port, "password": node.password}
            ]
        }
    elif node.protocol == "ss":
        settings = {
            "servers": [
                {
                    "address": node.server,
                    "port": node.port,
                    "method": node.method,
                    "password": node.password,
                }
            ]
        }
    elif node.protocol in ("socks", "http"):
        server: dict[str, Any] = {"address": node.server, "port": node.port}
        if node.uuid:
            server["users"] = [{"user": node.uuid, "pass": node.password, "level": 0}]
        settings = {"servers": [server]}
    else:
        raise ValueError(f"xray 不支持协议: {node.protocol}")

    return {
        "log": {"loglevel": log_level, "error": ""},
        "inbounds": [
            {
                "tag": "in",
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "socks",
                "settings": {"udp": True, "auth": "noauth"},
                "sniffing": {"enabled": False},
            },
            {
                "tag": "in-http",
                "listen": "127.0.0.1",
                "port": port + 10000 if port + 10000 < 65536 else port,
                "protocol": "http",
                "settings": {"allowTransparent": False},
                "sniffing": {"enabled": False},
            },
        ],
        "outbounds": [
            {"tag": "proxy", "protocol": kernel_protocol_name(node),
             "settings": settings, "streamSettings": _xr_stream(node)},
            {"tag": "direct", "protocol": "freedom"},
        ],
    }


# ---------------------------------------------------------------- 统一入口


def build_config(node: ParsedNode, kernel: str, port: int,
                 log_level: str = "error") -> dict[str, Any]:
    if kernel == "xray":
        return build_xray_config(node, port, log_level)
    return build_singbox_config(node, port, log_level)


def dumps(cfg: dict[str, Any]) -> str:
    """序列化为无 BOM 的 UTF-8 JSON 文本."""
    return json.dumps(cfg, ensure_ascii=False, indent=2)
