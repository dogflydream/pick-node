"""代理 URI 解析器.

把一行一个的代理 URI 解析成统一的 Node 结构, 供 config_gen 生成内核配置.

支持协议:
  - vless://      (含 Reality / XTLS-Vision / ws / grpc / httpupgrade)
  - vmess://      (base64 JSON, v2rayN 格式)
  - trojan://
  - ss://          (SIP002 与旧式 base64 两种写法)
  - hysteria2://   (别名 hy2://)
  - tuic://
  - socks:// / http:// (作为链式跳板场景的兜底)

设计原则: 解析失败绝不抛异常给调用方, 统一返回 ParsedNode.ok=False + error 说明,
这样批量导入 100 个节点时个别坏节点不会中断整体流程.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

# ---------------------------------------------------------------- 数据结构


@dataclass
class ParsedNode:
    """一个规范化后的节点."""

    raw: str
    protocol: str = ""            # vless / vmess / trojan / ss / hysteria2 / tuic ...
    tag: str = ""                 # 展示名
    server: str = ""
    port: int = 0
    uuid: str = ""
    password: str = ""
    method: str = ""              # ss 加密方式
    flow: str = ""
    alter_id: int = 0
    security: str = "auto"        # vmess 加密
    # 传输层
    network: str = "tcp"          # tcp / ws / grpc / http / httpupgrade / quic
    path: str = ""
    host: str = ""
    service_name: str = ""        # grpc
    # TLS
    tls: bool = False
    sni: str = ""
    alpn: list[str] = field(default_factory=list)
    fingerprint: str = ""
    allow_insecure: bool = False
    # Reality
    reality_pubkey: str = ""
    reality_short_id: str = ""
    # Hysteria2 / TUIC 扩展
    obfs: str = ""
    obfs_password: str = ""
    congestion: str = ""
    udp: bool = True
    # Hysteria2 端口跳跃 (mport=35000-39000) 与证书指纹 pinning
    server_ports: str = ""
    pin_sha256: str = ""

    ok: bool = False
    error: str = ""

    @property
    def node_id(self) -> str:
        """稳定唯一 ID: 取关键连接参数的短哈希, 同一节点重复导入不会产生歧义."""
        key = f"{self.protocol}|{self.server}|{self.port}|{self.uuid}|{self.password}|{self.method}"
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- 工具函数


def _b64_pad(s: str) -> str:
    """补全 base64 缺失的 padding, 并容忍 URL-safe 变体."""
    s = s.strip().replace("-", "+").replace("_", "/")
    return s + "=" * (-len(s) % 4)


def _b64decode(s: str) -> bytes:
    return base64.b64decode(_b64_pad(s))


def _b64decode_text(s: str) -> str:
    return _b64decode(s).decode("utf-8", errors="replace")


def _first(qs: dict[str, list[str]], *keys: str, default: str = "") -> str:
    for k in keys:
        if k in qs and qs[k]:
            return qs[k][0]
    return default


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def _split_hostport(netloc: str) -> tuple[str, int]:
    """拆分 host:port, 兼容 IPv6 的 [::1]:443 写法."""
    netloc = netloc.strip()
    if netloc.startswith("["):
        end = netloc.find("]")
        host = netloc[1:end]
        rest = netloc[end + 1 :]
        port = int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else 0
        return host, port
    if netloc.count(":") == 1:
        h, p = netloc.rsplit(":", 1)
        return h, int(p) if p.isdigit() else 0
    return netloc, 0


def _norm_alpn(raw: str) -> list[str]:
    if not raw:
        return []
    return [x for x in re.split(r"[,|]", raw) if x]


# ---------------------------------------------------------------- 各协议解析


def _parse_vless(uri: str) -> ParsedNode:
    n = ParsedNode(raw=uri)
    u = urlparse(uri)
    n.protocol = "vless"
    n.uuid = unquote(u.username or "")
    host, port = _split_hostport(u.netloc.split("@")[-1])
    n.server, n.port = host, port
    qs = parse_qs(u.query)

    n.flow = _first(qs, "flow")
    n.network = _first(qs, "type", "network", default="tcp").lower()
    n.path = unquote(_first(qs, "path"))
    n.host = _first(qs, "host")
    n.service_name = _first(qs, "serviceName", "servicename")
    n.sni = _first(qs, "sni", "peer")
    n.alpn = _norm_alpn(_first(qs, "alpn"))
    n.fingerprint = _first(qs, "fp", "fingerprint", default="chrome")
    n.allow_insecure = _truthy(_first(qs, "allowInsecure", "insecure"))

    sec = _first(qs, "security", default="none").lower()
    # Reality 与常规 TLS 都要求 tls.enabled=true, 由 security 决定细节
    n.tls = sec in ("tls", "reality", "xtls")
    if sec == "reality":
        n.reality_pubkey = _first(qs, "pbk", "publicKey")
        n.reality_short_id = _first(qs, "sid", "shortId")
    if n.network in ("ws", "httpupgrade") and not n.path:
        n.path = "/"
    n.tag = unquote(u.fragment) or f"{n.server}:{n.port}"
    return n


def _parse_vmess(uri: str) -> ParsedNode:
    """vmess:// 后面是 base64 编码的 JSON (v2rayN 事实标准)."""
    n = ParsedNode(raw=uri)
    n.protocol = "vmess"
    payload = uri[len("vmess://") :].strip()
    try:
        obj = json.loads(_b64decode_text(payload))
    except (binascii.Error, ValueError, UnicodeDecodeError) as e:
        n.error = f"vmess base64/JSON 解码失败: {e}"
        return n

    n.server = str(obj.get("add", "")).strip()
    try:
        n.port = int(obj.get("port", 0))
    except (TypeError, ValueError):
        n.port = 0
    n.uuid = str(obj.get("id", "")).strip()
    n.alter_id = int(obj.get("aid", 0) or 0)
    n.security = str(obj.get("scy", "auto") or "auto")
    n.network = str(obj.get("net", "tcp") or "tcp").lower()
    n.host = str(obj.get("host", "") or "")
    n.path = str(obj.get("path", "") or "")
    n.sni = str(obj.get("sni", "") or "")
    n.alpn = _norm_alpn(str(obj.get("alpn", "") or ""))
    n.fingerprint = str(obj.get("fp", "") or "")
    tls_flag = str(obj.get("tls", "") or "").lower()
    n.tls = tls_flag in ("tls", "reality", "true", "1")
    if n.tls and not n.sni:
        n.sni = n.host or n.server
    n.tag = str(obj.get("ps", "") or "") or f"{n.server}:{n.port}"
    if n.network == "h2":
        # sing-box 1.13 已用 http 取代 h2
        n.network = "http"
    return n


def _parse_trojan(uri: str) -> ParsedNode:
    n = ParsedNode(raw=uri)
    u = urlparse(uri)
    n.protocol = "trojan"
    n.password = unquote(u.username or "")
    host, port = _split_hostport(u.netloc.split("@")[-1])
    n.server, n.port = host, port
    qs = parse_qs(u.query)
    n.network = _first(qs, "type", default="tcp").lower()
    n.path = unquote(_first(qs, "path"))
    n.host = _first(qs, "host")
    n.service_name = _first(qs, "serviceName", "servicename")
    n.sni = _first(qs, "sni", "peer")
    n.alpn = _norm_alpn(_first(qs, "alpn"))
    n.fingerprint = _first(qs, "fp", "fingerprint", default="chrome")
    n.allow_insecure = _truthy(_first(qs, "allowInsecure", "insecure"))
    # Trojan 本身即 TLS, 默认开启
    sec = _first(qs, "security", default="tls").lower()
    n.tls = sec != "none"
    if n.network in ("ws", "httpupgrade") and not n.path:
        n.path = "/"
    n.tag = unquote(u.fragment) or f"{n.server}:{n.port}"
    return n


def _parse_ss(uri: str) -> ParsedNode:
    """Shadowsocks: 兼容 SIP002 (userinfo@host:port) 与旧式整段 base64."""
    n = ParsedNode(raw=uri)
    n.protocol = "ss"
    body = uri[len("ss://") :]

    # 先摘掉 fragment 作为 tag
    frag = ""
    if "#" in body:
        body, frag = body.split("#", 1)
        frag = unquote(frag)

    qs: dict[str, list[str]] = {}
    if "?" in body:
        body, q = body.split("?", 1)
        qs = parse_qs(q)

    userinfo = ""
    hostport = ""
    if "@" in body:
        # SIP002: base64(method:password)@host:port, 有时 userinfo 未编码
        userinfo, hostport = body.rsplit("@", 1)
        if ":" not in userinfo:
            try:
                userinfo = _b64decode_text(userinfo)
            except (binascii.Error, ValueError, UnicodeDecodeError):
                pass
    else:
        # 旧式: 整段 base64(method:password@host:port)
        try:
            decoded = _b64decode_text(body)
        except (binascii.Error, ValueError, UnicodeDecodeError) as e:
            n.error = f"ss base64 解码失败: {e}"
            return n
        if "@" not in decoded:
            n.error = "ss 解码后缺少 '@' 分隔"
            return n
        userinfo, hostport = decoded.rsplit("@", 1)

    if ":" not in userinfo:
        n.error = "ss userinfo 缺少 method:password"
        return n
    n.method, n.password = userinfo.split(":", 1)
    n.method = n.method.strip().lower()
    n.password = unquote(n.password)

    n.server, n.port = _split_hostport(hostport)
    if n.port == 0:
        n.port = int(_first(qs, "port", default="0") or 0)

    plugin = _first(qs, "plugin")
    if plugin:
        # 仅记录, 生成配置时按需映射 (obfs 类插件)
        if plugin.startswith("obfs-local") or plugin.startswith("simple-obfs"):
            for part in plugin.split(";"):
                if part.startswith("obfs="):
                    n.obfs = part.split("=", 1)[1]
                elif part.startswith("obfs-host="):
                    n.obfs_password = part.split("=", 1)[1]

    n.tag = frag or f"{n.server}:{n.port}"
    return n


def _parse_hysteria2(uri: str) -> ParsedNode:
    n = ParsedNode(raw=uri)
    u = urlparse(uri)
    n.protocol = "hysteria2"
    # hysteria2://password@host:port  或  hysteria2://user:pass@host:port
    n.password = unquote(u.username or "")
    if u.password:
        n.password = f"{n.password}:{unquote(u.password)}"
    host, port = _split_hostport(u.netloc.split("@")[-1])
    n.server, n.port = host, port
    qs = parse_qs(u.query)
    n.sni = _first(qs, "sni", "peer")
    n.allow_insecure = _truthy(_first(qs, "insecure", "allowInsecure"))
    n.alpn = _norm_alpn(_first(qs, "alpn"))
    n.obfs = _first(qs, "obfs")
    n.obfs_password = _first(qs, "obfs-password", "obfs_password")
    # 端口跳跃: mport=35000-39000 (sing-box 的 server_ports)
    n.server_ports = _first(qs, "mport", "ports")
    # 证书指纹固定 (CA 不可信时用于校验)
    n.pin_sha256 = _first(qs, "pinSHA256", "pinsha256", "pin_sha256")
    n.tls = True  # Hysteria2 强制 TLS
    n.tag = unquote(u.fragment) or f"{n.server}:{n.port}"
    return n


def _parse_tuic(uri: str) -> ParsedNode:
    n = ParsedNode(raw=uri)
    u = urlparse(uri)
    n.protocol = "tuic"
    n.uuid = unquote(u.username or "")
    n.password = unquote(u.password or "")
    host, port = _split_hostport(u.netloc.split("@")[-1])
    n.server, n.port = host, port
    qs = parse_qs(u.query)
    n.sni = _first(qs, "sni", "peer")
    n.alpn = _norm_alpn(_first(qs, "alpn"))
    n.allow_insecure = _truthy(_first(qs, "allow_insecure", "insecure"))
    n.congestion = _first(qs, "congestion_control", "congestion")
    n.tls = True
    n.tag = unquote(u.fragment) or f"{n.server}:{n.port}"
    return n


def _parse_socks_http(uri: str) -> ParsedNode:
    n = ParsedNode(raw=uri)
    u = urlparse(uri)
    n.protocol = "socks" if uri.startswith("socks") else "http"
    n.server, n.port = _split_hostport(u.netloc.split("@")[-1])
    if u.username:
        n.uuid = unquote(u.username)
        n.password = unquote(u.password or "")
    n.tag = unquote(u.fragment) or f"{n.server}:{n.port}"
    return n


_PARSERS = {
    "vless": _parse_vless,
    "vmess": _parse_vmess,
    "trojan": _parse_trojan,
    "ss": _parse_ss,
    "hysteria2": _parse_hysteria2,
    "hy2": _parse_hysteria2,
    "tuic": _parse_tuic,
    "socks": _parse_socks_http,
    "socks5": _parse_socks_http,
    "http": _parse_socks_http,
    "https": _parse_socks_http,
}

# sing-box 支持、但我们按“暂不支持”明确拒绝的协议, 便于给出清晰报错
_KNOWN_UNSUPPORTED = {
    "ssr": "ShadowsocksR (建议改用 ss/hysteria2)",
    "hysteria": "Hysteria v1 (建议改用 hysteria2)",
    "wireguard": "WireGuard",
    "ssh": "SSH",
    "tor": "Tor",
    "juicity": "Juicity",
    "anytls": "AnyTLS",
}


def parse_uri(uri: str) -> ParsedNode:
    """解析单条 URI, 永不抛异常."""
    raw = uri.strip()
    if not raw:
        n = ParsedNode(raw=uri)
        n.error = "空行"
        return n
    if raw.startswith("#") or raw.startswith("//"):
        n = ParsedNode(raw=uri)
        n.error = "注释行"
        return n

    m = re.match(r"^([A-Za-z0-9+.-]+)://", raw)
    if not m:
        n = ParsedNode(raw=uri)
        n.error = "不是合法的 URI (缺少 scheme://)"
        return n

    scheme = m.group(1).lower()
    if scheme in _KNOWN_UNSUPPORTED:
        n = ParsedNode(raw=uri)
        n.protocol = scheme
        n.error = f"暂不支持的协议: {_KNOWN_UNSUPPORTED[scheme]}"
        return n

    parser = _PARSERS.get(scheme)
    if parser is None:
        n = ParsedNode(raw=uri)
        n.protocol = scheme
        n.error = f"未知协议: {scheme}"
        return n

    try:
        n = parser(raw)
    except Exception as e:  # 解析器内部任何意外都收敛为一条错误
        n = ParsedNode(raw=uri)
        n.protocol = scheme
        n.error = f"解析异常: {type(e).__name__}: {e}"
        return n

    if not n.server:
        n.error = n.error or "缺少服务器地址"
        return n
    if not (0 < n.port < 65536):
        n.error = n.error or f"端口非法: {n.port}"
        return n
    if n.protocol in ("vless", "vmess") and not n.uuid:
        n.error = "缺少 UUID"
        return n
    if n.protocol == "ss" and not n.method:
        n.error = "缺少加密方式"
        return n
    if n.protocol in ("trojan", "hysteria2") and not n.password:
        n.error = "缺少密码"
        return n

    if not n.sni and n.tls:
        n.sni = n.host or n.server
    n.ok = True
    return n


def parse_many(text: str) -> tuple[list[ParsedNode], list[ParsedNode]]:
    """解析多行文本, 返回 (可用节点, 失败节点)."""
    good: list[ParsedNode] = []
    bad: list[ParsedNode] = []
    seen: set[str] = set()

    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            continue
        n = parse_uri(s)
        if not n.ok:
            bad.append(n)
            continue
        # 按 node_id 去重, 保证同一节点只测一次
        nid = n.node_id
        if nid in seen:
            continue
        seen.add(nid)
        good.append(n)
    return good, bad


def load_nodes_file(path: str) -> tuple[list[ParsedNode], list[ParsedNode]]:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return parse_many(f.read())
