"""出口检测器: 判断节点的出口 IP / 地区 / Google 服务可用性.

设计依据 (均为本机实测结论, 不是猜测):

1) 出口 IP 与地区
   - 首选 https://www.cloudflare.com/cdn-cgi/trace: 返回纯文本, 含 ip= 和 loc=,
     由 Cloudflare 边缘节点权威判定, 速度快且无配额限制.
   - 备用多个 geo-IP API. 实测这些 API 对同一 IP 可能给出不同国家
     (例如某 IP: ipinfo=US, ip-api=CN), 因此以 trace 为主, API 仅作补强,
     并在结果中保留多个来源供人工判断.

2) Google 可达性 / 地区限制
   - 关键发现: 被地区限制时, google.com 会 302 跳到 google.com.hk 之类的
     "本地化域名", 且 Location 里带 pref=hkredirect / hl=xx 参数.
     因此不能只看 "是否 200", 而要解析最终域名与重定向链.
   - 直连 China/HK 出口时 gemini.google.com 仍返回 200 且不跳转,
     所以 "URL 未跳转" 不足以判定 Gemini 可用 —— 必须结合出口地区综合判断.

3) Gemini / Antigravity 可用性
   - https://www.google.com/sorry/index 在被判定为滥用/受限时返回 429,
     这是一个明确、可机检的信号.
   - Gemini 真正的区域门禁在服务端 API 与会话层, 纯 HTML 里没有稳定的
     "不可用" 文案 (实测页面 792KB, 无 "not available in your country").
     故采用分层判定: 地区 + google.com 是否跳转 + sorry 检测 + 目标页可达性.
     结果标注置信度, 明确区分 "确定可用" / "可能可用" / "不可用".
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# 谷歌系服务的本地化域名后缀: 出现即代表被重定向到某地区版本
_GOOGLE_TLD_RE = re.compile(r"^https?://(www\.)?google\.([a-z.]+)/", re.I)

# 已知的受限/本地化跳转目标 -> 地区码
_TLD_TO_COUNTRY = {
    "com.hk": "HK", "com.tw": "TW", "com.sg": "SG", "co.jp": "JP",
    "co.kr": "KR", "com.cn": "CN", "cn": "CN", "co.uk": "GB",
    "de": "DE", "fr": "FR", "com.au": "AU", "ca": "CA", "com.br": "BR",
    "ru": "RU", "in": "IN", "com.mx": "MX", "it": "IT", "nl": "NL",
}

# 明确不支持 Gemini 的地区 (依据 Google 官方支持列表的常见排除项)
_GEMINI_BLOCKED_REGIONS = {"CN", "HK", "RU", "IR", "KP", "CU", "SY", "VE"}

# 这些地区通常可以使用 Gemini / Antigravity
_GEMINI_ALLOWED_REGIONS = {
    "US", "JP", "SG", "TW", "KR", "GB", "DE", "FR", "CA", "AU",
    "NL", "IT", "ES", "SE", "CH", "PL", "IN", "BR", "ID", "TH",
    "VN", "MY", "PH", "MX", "TR", "AE", "IL", "NZ", "IE", "BE",
    "AT", "DK", "NO", "FI", "PT", "CZ", "GR", "AR", "CL", "CO",
}

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

# ---------------------------------------------------------------- 风险重定向判定
#
# 背景 (实测依据, 见 README 第 8 节):
#   用 http://www.google.com 做探针时, 观察重定向/最终 URL 里是否出现风险关键词,
#   可以识别出被 Google 判定为"代理/VPN 出口"的节点 —— 这类节点 Gemini 与
#   Antigravity 实际都不可用:
#     - Gemini 会提示"你所在的地区不支持"
#     - Antigravity 客户端登录会报 token: eof
#
#   实测证据: 带 pref=hkredirect 的节点, 访问 https://gemini.google.com/app
#   会被 302 送到 https://www.google.com/sorry/index (Google 风控拦截页),
#   而不带该标记的节点 /app 直接 200. 这是比 URL 字符串匹配更硬的证据.
#
# 判定分级:
#   high —— 命中明确的风险信号 (sorry 风控页 / pref=hkredirect 且 /app 被拦)
#   low  —— URL 中出现可疑关键词, 但未拿到硬证据
#   none —— 无异常

# 风险关键词: 出现在 google.com 的重定向 Location 或最终 URL 中
#   hk / cn  —— Google 已知对这两个地区限制 AI 服务
#   sorry / error / captcha / unusual —— 风控与异常流量标记
_RISK_KEYWORDS = {
    "hkredirect": "high",
    "sorry": "high",
    "captcha": "high",
    "unusual": "high",
    "error": "low",
    "com.hk": "high",
    "com.cn": "high",
    "hl=cn": "high",
}

# 明确限制 AI 服务的 Google 本地化域名
_RESTRICTED_TLDS = ("com.hk", "com.cn", "cn", "co.kr")


# ---------------------------------------------------------------- 结果结构


@dataclass
class ProbeResult:
    """一次 HTTP 探测的结果."""

    name: str
    ok: bool = False
    status: int = 0
    final_url: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str = ""
    server: str = ""
    location: str = ""


@dataclass
class Detection:
    """一个节点的完整检测结论."""

    ip: str = ""
    country: str = ""            # ISO 国家码, 如 JP
    country_source: str = ""     # 该结论来自哪个来源
    country_alt: dict[str, str] = field(default_factory=dict)  # 其他来源的结论
    city: str = ""
    asn_org: str = ""
    # 服务可用性
    google: str = "fail"         # success / fail
    google_status: int = 0
    google_final_url: str = ""
    # 风险信号 (来自 google.com 的重定向行为)
    google_redirected: bool = False   # google.com 是否被重定向到其它域名
    hk_redirect: bool = False         # 命中 pref=hkredirect (Google 按中文用户本地化)
    risk_flags: list[str] = field(default_factory=list)  # 命中的风险关键词
    risk_level: str = "none"          # none / low / high
    risk_reason: str = ""
    gemini: str = "unknown"      # available / blocked / unavailable / unknown
    gemini_reason: str = ""
    antigravity: str = "unknown"
    antigravity_reason: str = ""
    latency: int = 0             # 到出口的延迟 (ms), 取最快探测
    tls_ok: bool = False
    reachable: bool = False      # 代理链路本身是否通
    errors: list[str] = field(default_factory=list)
    probes: list[dict[str, Any]] = field(default_factory=list)
    confidence: str = "low"      # 结论置信度 high / medium / low


# ---------------------------------------------------------------- 探测器


class Detector:
    """针对单个本地代理端口做出口检测."""

    def __init__(
        self,
        timeout: float = 12.0,
        do_antigravity: bool = True,
        do_geoip_fallback: bool = True,
    ):
        self.timeout = timeout
        self.do_antigravity = do_antigravity
        self.do_geoip_fallback = do_geoip_fallback

    # -------------------------------------------------- 底层请求

    def _client(self, proxy_url: str) -> httpx.Client:
        # socks5:// 让 DNS 也在代理侧解析, 保证按出口地区解析域名.
        #
        # trust_env=False 至关重要: 本机装有 v2rayN 并设置了系统代理
        # (注册表 ProxyServer=127.0.0.1:3067). 若不关闭 trust_env, httpx 会读取
        # 系统/环境代理设置, 于是"通过节点代理"的请求实际上走了 v2rayN 的出口,
        # 导致所有节点都报告同一个出口 IP —— 全部误判为可用.
        return httpx.Client(
            proxy=proxy_url,
            trust_env=False,
            timeout=httpx.Timeout(self.timeout),
            headers=_HEADERS,
            follow_redirects=True,
            verify=False,  # 部分节点自签证书, 不因证书问题误判为不可用
        )

    def _probe(self, client: httpx.Client, name: str, url: str) -> ProbeResult:
        """请求一个 URL, 记录状态码/最终 URL/重定向链/耗时."""
        pr = ProbeResult(name=name)
        t0 = time.perf_counter()
        try:
            r = client.get(url)
            pr.elapsed_ms = int((time.perf_counter() - t0) * 1000)
            pr.status = r.status_code
            pr.final_url = str(r.url)
            pr.server = r.headers.get("server", "")
            pr.redirect_chain = [str(h.headers.get("location", "")) for h in r.history
                                 if h.headers.get("location")]
            pr.ok = True
        except Exception as e:
            pr.elapsed_ms = int((time.perf_counter() - t0) * 1000)
            pr.error = f"{type(e).__name__}: {str(e)[:160]}"
        return pr

    # -------------------------------------------------- 出口 IP / 地区

    def detect_exit(self, proxy_url: str, det: Detection) -> None:
        """通过代理获取出口 IP 与国家."""
        with self._client(proxy_url) as client:
            # 1) Cloudflare trace —— 权威且轻量
            try:
                r = client.get("https://www.cloudflare.com/cdn-cgi/trace")
                if r.status_code == 200:
                    kv = {}
                    for line in r.text.strip().splitlines():
                        if "=" in line:
                            k, v = line.split("=", 1)
                            kv[k.strip()] = v.strip()
                    ip = kv.get("ip", "")
                    loc = kv.get("loc", "")
                    if ip:
                        det.ip = ip
                        det.country = loc.upper() if loc else ""
                        det.country_source = "cloudflare-trace"
                        det.reachable = True
                    det.probes.append({"name": "cf-trace", "status": r.status_code,
                                       "ip": ip, "loc": loc})
            except Exception as e:
                det.errors.append(f"cf-trace: {type(e).__name__}: {str(e)[:100]}")

            # 2) geo-IP 补强 (结果可能与 trace 不同, 一并保留)
            if self.do_geoip_fallback and not det.ip:
                for url, key_ip, key_cc, key_city, key_org in (
                    ("https://ipinfo.io/json", "ip", "country", "city", "org"),
                    ("https://api.ip.sb/geoip", "ip", "country_code", "city", "isp"),
                ):
                    try:
                        r = client.get(url)
                        if r.status_code != 200:
                            continue
                        j = r.json()
                        ip = str(j.get(key_ip, ""))
                        if not ip:
                            continue
                        if not det.ip:
                            det.ip = ip
                            det.country = str(j.get(key_cc, "")).upper()
                            det.country_source = url.split("/")[2]
                            det.reachable = True
                        det.city = str(j.get(key_city, "") or "")
                        det.asn_org = str(j.get(key_org, "") or "")
                        det.country_alt[url.split("/")[2]] = str(j.get(key_cc, "")).upper()
                        break
                    except Exception:
                        continue

            # 即便 trace 成功, 也顺带取一次 city/org 以丰富输出
            if det.ip and not det.asn_org:
                try:
                    r = client.get("https://ipinfo.io/json")
                    if r.status_code == 200:
                        j = r.json()
                        det.city = det.city or str(j.get("city", "") or "")
                        det.asn_org = det.asn_org or str(j.get("org", "") or "")
                        cc = str(j.get("country", "") or "").upper()
                        if cc and cc != det.country:
                            det.country_alt["ipinfo.io"] = cc
                except Exception:
                    pass

    # -------------------------------------------------- Google / Gemini

    def detect_google(self, proxy_url: str, det: Detection) -> None:
        """检测 google.com 可达性与地区跳转."""
        with self._client(proxy_url) as client:
            pr = self._probe(client, "google", "https://www.google.com/")
            det.probes.append({
                "name": "google", "status": pr.status, "final_url": pr.final_url,
                "elapsed_ms": pr.elapsed_ms, "error": pr.error,
                "chain": pr.redirect_chain,
            })
            det.google_status = pr.status
            det.google_final_url = pr.final_url
            det.latency = pr.elapsed_ms if pr.elapsed_ms else det.latency

            if not pr.ok:
                det.google = "fail"
                det.errors.append(f"google: {pr.error}")
                return

            if pr.status == 200 and pr.final_url:
                det.google = "success"
                det.reachable = True
                # 从重定向 URL 里提取 Google 认定的地区
                m = _GOOGLE_TLD_RE.match(pr.final_url)
                if m:
                    tld = m.group(2).lower()
                    cc = _TLD_TO_COUNTRY.get(tld)
                    if cc:
                        det.country_alt["google-redirect-tld"] = cc
                        # 若 trace 没拿到地区, 用跳转地区兜底
                        if not det.country:
                            det.country = cc
                            det.country_source = "google-redirect"
                # pref=xxredirect 也直接给出地区
                mm = re.search(r"pref=([a-z]{2})redirect", pr.final_url, re.I)
                if mm:
                    det.country_alt["google-pref"] = mm.group(1).upper()
            else:
                det.google = "fail"

            # sorry 检测: 429 表示被判定为异常流量
            prs = self._probe(client, "sorry", "https://www.google.com/sorry/index")
            if prs.status == 429:
                det.google = "success"       # 能连上 google, 但被判风控
                det.probes.append({"name": "sorry", "status": 429,
                                   "note": "CAPTCHA/风控"})

            # 风险重定向分析 (hkredirect / hk / cn / error ...)
            self._analyze_risk(client, det, pr)

    def _analyze_risk(self, client: httpx.Client, det: Detection,
                      google_probe: ProbeResult) -> None:
        """分析 google.com 的重定向行为, 识别被判定为代理/VPN 的节点.

        判定步骤:
          1. 收集 google.com 的重定向 Location 链 + 最终 URL
          2. 在其中匹配风险关键词 (hkredirect / com.hk / cn / sorry / error ...)
          3. 关键加固证据: 探测 https://gemini.google.com/app
             若被 302 送到 google.com/sorry, 说明 Google 已对该出口实施风控拦截,
             这类节点 Gemini 与 Antigravity 实际都不可用.
        """
        # ---- 1) 关键词匹配
        final = (google_probe.final_url or "").lower()
        chain = " ".join(google_probe.redirect_chain or []).lower()
        haystack = chain + " " + final

        flags: list[str] = []
        for kw, level in _RISK_KEYWORDS.items():
            if kw in haystack and kw not in flags:
                flags.append(kw)

        det.google_redirected = bool(google_probe.redirect_chain) or bool(
            final and "www.google.com/" not in final.rstrip("/") + "/"
        )
        det.hk_redirect = "hkredirect" in haystack
        det.risk_flags = flags

        # 记录重定向链, 便于人工复核
        if google_probe.redirect_chain:
            det.probes.append({
                "name": "google-redirect-chain",
                "chain": google_probe.redirect_chain[:4],
            })

        # ---- 2) 硬证据: gemini/app 是否被送到 sorry 风控页
        app_flagged = False
        app_location = ""
        try:
            ra = client.get("https://gemini.google.com/app", follow_redirects=False)
            app_location = ra.headers.get("location", "") or ""
            det.probes.append({
                "name": "gemini-app",
                "status": ra.status_code,
                "location": app_location[:180],
            })
            if "sorry" in app_location.lower() or "sorry" in str(ra.url).lower():
                app_flagged = True
        except Exception as e:
            det.errors.append(f"gemini-app: {type(e).__name__}: {str(e)[:80]}")

        # ---- 3) 汇总风险等级
        has_high_kw = any(_RISK_KEYWORDS.get(f) == "high" for f in flags)
        if app_flagged:
            det.risk_level = "high"
            det.risk_reason = (
                "gemini.google.com/app 被重定向到 google.com/sorry "
                "(Google 风控拦截) —— 该出口被判定为代理/VPN, "
                "Gemini 会提示地区不支持, Antigravity 登录会失败"
            )
        elif det.hk_redirect and has_high_kw:
            det.risk_level = "high"
            det.risk_reason = (
                f"google.com 本地化重定向命中风险标记 {flags} "
                "(pref=hkredirect, Google 按中文用户/受限地区策略处理) —— "
                "该出口被判定为代理/VPN, Gemini 与 Antigravity 实际不可用"
            )
        elif has_high_kw:
            det.risk_level = "high"
            det.risk_reason = f"google.com 重定向命中风险标记 {flags}"
        elif flags:
            det.risk_level = "low"
            det.risk_reason = f"google.com URL 中出现可疑关键词 {flags} (弱信号)"
        else:
            det.risk_level = "none"

    def detect_gemini(self, proxy_url: str, det: Detection) -> None:
        """分层判定 Gemini 可用性.

        重要前提 (实测): gemini.google.com 无论从受限还是正常地区访问, 都会返回
        约 792KB 的应用外壳页面, 且页面内不含国家码, 也没有 "not available in your
        country" 之类文案 —— 地区门禁是在浏览器端/登录后 API 层决定的.
        因此单凭 HTTP 200 不能证明 Gemini 可用.

        判定策略是按证据强度分层:
          blocked     : 命中明确的反面信号 (sorry 页 / 429 / 跳离 gemini 域)
          available   : 出口地区在支持列表 且 页面可达  -> 高概率可用
          likely      : 地区未知或不在已知列表, 页面可达 -> 需人工确认
          unavailable : 请求层面就失败
        并在 reason 里写明依据, 不给出无法支撑的确定性结论.
        """
        with self._client(proxy_url) as client:
            pr = self._probe(client, "gemini", "https://gemini.google.com/")
            det.probes.append({
                "name": "gemini", "status": pr.status, "final_url": pr.final_url,
                "elapsed_ms": pr.elapsed_ms, "error": pr.error,
                "chain": pr.redirect_chain,
            })

            if not pr.ok:
                det.gemini = "unavailable"
                det.gemini_reason = f"请求失败: {pr.error}"
                return

            final = pr.final_url.lower()
            # 明确失败信号: 跳到 sorry / 被重定向到非 gemini 域
            if "/sorry/" in final or "sorry/index" in final:
                det.gemini = "blocked"
                det.gemini_reason = "被重定向到 google.com/sorry (风控验证)"
                return
            if pr.status == 429:
                det.gemini = "blocked"
                det.gemini_reason = "HTTP 429 风控"
                return
            if pr.status >= 400:
                det.gemini = "unavailable"
                det.gemini_reason = f"HTTP {pr.status}"
                return

            # 页面结构判断: 被重定向离开 gemini 域即视为不可用
            if "gemini.google.com" not in final:
                det.gemini = "unavailable"
                det.gemini_reason = f"被重定向到 {pr.final_url[:80]}"
                return

            # 结合地区白名单给出结论 (页面可达 + 地区已知支持 => 较可信)
            cc = (det.country or "").upper()
            if cc in _GEMINI_BLOCKED_REGIONS:
                det.gemini = "blocked"
                det.gemini_reason = f"出口地区 {cc} 不在 Gemini 支持范围"
            elif cc in _GEMINI_ALLOWED_REGIONS:
                det.gemini = "available"
                det.gemini_reason = f"出口地区 {cc} (支持列表), 页面可达"
            else:
                det.gemini = "likely"
                det.gemini_reason = (
                    f"出口地区 {cc or '未知'}, 页面可达 —— "
                    "Gemini 区域门禁在客户端, 建议登录实测"
                )

            # ---- 风险重定向覆盖 (优先级最高, 由 detect_google 阶段判定) ----
            # 实测: 出口被 Google 判定为代理/VPN 时 (表现为 google.com 跳
            # com.hk 且带 pref=hkredirect, 或 gemini/app 被送到 sorry),
            # 即使出口在 SG/JP/US 且页面返回 200, Gemini 实际仍不可用
            # (提示"所在地区不支持"), Antigravity 登录也会失败 (token: eof).
            # 这种情况下不可判为 available.
            if det.risk_level == "high" and det.gemini == "available":
                det.gemini = "blocked"
                det.gemini_reason = f"风险重定向: {det.risk_reason}"

    def detect_antigravity(self, proxy_url: str, det: Detection) -> None:
        """检测 antigravity.google 可达性."""
        with self._client(proxy_url) as client:
            pr = self._probe(client, "antigravity", "https://antigravity.google/")
            det.probes.append({
                "name": "antigravity", "status": pr.status,
                "final_url": pr.final_url, "elapsed_ms": pr.elapsed_ms,
                "error": pr.error, "chain": pr.redirect_chain,
            })
            if not pr.ok:
                det.antigravity = "unavailable"
                det.antigravity_reason = f"请求失败: {pr.error}"
                return
            final = pr.final_url.lower()
            if "/sorry/" in final or pr.status == 429:
                det.antigravity = "blocked"
                det.antigravity_reason = "风控/验证页"
                return
            if pr.status >= 400:
                det.antigravity = "unavailable"
                det.antigravity_reason = f"HTTP {pr.status}"
                return
            if "antigravity.google" not in final:
                det.antigravity = "unavailable"
                det.antigravity_reason = f"被重定向到 {pr.final_url[:80]}"
                return

            cc = (det.country or "").upper()
            if cc in _GEMINI_BLOCKED_REGIONS:
                det.antigravity = "blocked"
                det.antigravity_reason = f"出口地区 {cc} 受限"
            elif cc in _GEMINI_ALLOWED_REGIONS:
                det.antigravity = "available"
                det.antigravity_reason = f"出口地区 {cc}, 页面可达"
            else:
                det.antigravity = "likely"
                det.antigravity_reason = f"出口地区 {cc or '未知'}, 页面可达"

            # ---- 风险重定向覆盖 (同 Gemini) ----
            # 实测: 出口被判定为代理/VPN (hkredirect / sorry) 时,
            # Antigravity 客户端登录会报 token: eof, 实际不可用.
            if det.risk_level == "high" and det.antigravity == "available":
                det.antigravity = "blocked"
                det.antigravity_reason = (
                    f"风险重定向: {det.risk_reason}"
                    " (Antigravity 客户端登录会失败)"
                )

    # -------------------------------------------------- 主流程

    def run(self, proxy_url: str) -> Detection:
        det = Detection()
        # 1) 出口 IP/地区
        self.detect_exit(proxy_url, det)
        # 2) Google
        self.detect_google(proxy_url, det)
        # 3) Gemini
        self.detect_gemini(proxy_url, det)
        # 4) Antigravity
        if self.do_antigravity:
            self.detect_antigravity(proxy_url, det)

        # 置信度: 有权威地区来源 + google 成功 => high
        if det.country_source == "cloudflare-trace" and det.google == "success":
            det.confidence = "high"
        elif det.country and det.google == "success":
            det.confidence = "medium"
        else:
            det.confidence = "low"
        return det


def country_name(cc: str) -> str:
    """ISO 国家码 -> 英文名 (仅覆盖常用项, 避免引入额外依赖)."""
    if not cc:
        return "Unknown"
    return {
        "US": "United States", "JP": "Japan", "SG": "Singapore", "HK": "Hong Kong",
        "TW": "Taiwan", "KR": "South Korea", "CN": "China", "GB": "United Kingdom",
        "DE": "Germany", "FR": "France", "CA": "Canada", "AU": "Australia",
        "NL": "Netherlands", "IT": "Italy", "ES": "Spain", "SE": "Sweden",
        "CH": "Switzerland", "PL": "Poland", "IN": "India", "BR": "Brazil",
        "ID": "Indonesia", "TH": "Thailand", "VN": "Vietnam", "MY": "Malaysia",
        "PH": "Philippines", "MX": "Mexico", "TR": "Turkey", "AE": "UAE",
        "IL": "Israel", "NZ": "New Zealand", "IE": "Ireland", "BE": "Belgium",
        "AT": "Austria", "DK": "Denmark", "NO": "Norway", "FI": "Finland",
        "PT": "Portugal", "CZ": "Czechia", "GR": "Greece", "RU": "Russia",
        "AR": "Argentina", "CL": "Chile", "CO": "Colombia", "IR": "Iran",
    }.get(cc.upper(), cc.upper())
