"""Cloudflare 响应分类 —— 区分「盾」与「封禁」.

这是本项目判定可用性的关键一环, 因为**不能只看状态码**:

  403 + CF 拦截页  ->  Cloudflare WAF 认为这个出口是恶意流量, 直接拒绝.
                      真实浏览器同样进不去 => 节点不可用.
  403 + CF 盾/挑战 ->  Cloudflare 只是要验证"你是不是真人"(JS 挑战 / Turnstile).
                      真实浏览器会自动通过 => 节点其实是可用的, 不能判死.
  403 + 其它内容   ->  站点自身的权限拒绝 (例如需要登录), 与代理质量无关.

实测样本 (grok.com 经某节点, 见仓库 test_grok.html) 属于第一类:
  <title>Attention Required! | Cloudflare</title>
  <h1 data-translate="block_headline">Sorry, you have been blocked</h1>
  <h2>You are unable to access grok.com</h2>
  <span class="cf-footer-item">Cloudflare Ray ID: ...</span>
这种是明确的"被封", 判不可用.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------- 分类结果

# 判定档位
CF_BLOCKED = "cf_blocked"          # CF 明确封禁 -> 不可用
CF_CHALLENGE = "cf_challenge"      # CF 盾/人机验证 -> 节点可用, 浏览器可过
CF_RATE_LIMITED = "cf_rate_limited"  # CF 限流 (1015 / 429)
CF_ERROR_5XX = "cf_error"          # CF 侧 5xx
SITE_DENIED = "site_denied"        # 站点自身拒绝 (非 CF), 如需登录
SITE_OK = "site_ok"                # 正常页面
UNKNOWN_DENIED = "unknown_denied"  # 非 2xx 但无法归类

# 人类可读标签
LABELS = {
    CF_BLOCKED: "CF 封禁",
    CF_CHALLENGE: "CF 人机验证",
    CF_RATE_LIMITED: "CF 限流",
    CF_ERROR_5XX: "CF 5xx",
    SITE_DENIED: "站点拒绝",
    SITE_OK: "正常",
    UNKNOWN_DENIED: "未知拒绝",
}

# 哪些档位意味着"这个节点不可用"
UNAVAILABLE_KINDS = {CF_BLOCKED, CF_RATE_LIMITED, CF_ERROR_5XX,
                     SITE_DENIED, UNKNOWN_DENIED}


@dataclass
class Classification:
    """一次响应的 Cloudflare 归类."""

    kind: str = SITE_OK
    reason: str = ""
    is_cloudflare: bool = False     # 响应是否来自 Cloudflare
    ray_id: str = ""                # CF Ray ID (排查用)
    blocked_host: str = ""          # "You are unable to access <host>"
    challenge_type: str = ""        # js / turnstile / managed

    @property
    def available(self) -> bool:
        """该响应是否说明节点可用."""
        return self.kind not in UNAVAILABLE_KINDS

    @property
    def label(self) -> str:
        return LABELS.get(self.kind, self.kind)


# ---------------------------------------------------------------- 特征匹配

# --- 强特征: CF 封禁页 (对应 test_grok.html) ---
# 注意: 不要用 "attention required! | cloudflare" —— 实测该标题在
# CF 的【挑战页】与【封禁页】都会出现, 属于通用错误页标题, 用它会把
# "盾" 误判成 "封禁". 这里只保留封禁页独有的特征.
_CF_BLOCK_MARKERS = (
    "cf-error-details",              # 封禁页专用容器 id
    'data-translate="block_headline"',
    "blocked_headline",
    "sorry, you have been blocked",
    "you are unable to access",
    "why have i been blocked",
    "blocked_why_headline",
)

# --- CF 盾 / 人机验证特征 ---
_CF_CHALLENGE_MARKERS = (
    "/cdn-cgi/challenge-platform/",       # JS 挑战脚本
    "cf-challenge",
    "cf_chl_opt",                         # 挑战参数
    "turnstile",                          # Turnstile 组件
    "challenges.cloudflare.com",
    "verify you are human",
    "checking your browser before accessing",
    "just a moment...",
    "enable javascript and cookies to continue",
    "cf-mitigated",
    "_cf_chl_",
)

# --- CF 限流 / 5xx ---
_CF_RATE_MARKERS = (
    "error 1015",
    "you are being rate limited",
    "rate limited",
    "too many requests",
)
_CF_5XX_MARKERS = (
    "error 1020",          # access denied
    "error 1006",
    "error 1005",
    "cf-error-code",
    "cloudflare ray id",
)

# --- 站点自身拒绝 (非 CF) ---
_SITE_DENY_MARKERS = (
    "access denied",
    "forbidden",
    "unauthorized",
    "please log in",
    "sign in to continue",
    "requires authentication",
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_RAY_RE = re.compile(
    r"Cloudflare Ray ID:?\s*(?:</?[^>]*>|\s)*([0-9a-f]{8,})", re.I)
# 实测结构: <span data-translate="unable_to_access">You are unable to access
#            </span> grok.com</h2>
# 站名在 </span> 之后, 不是标签内。
_BLOCKED_HOST_RE = re.compile(
    r'data-translate="unable_to_access"[^>]*>.*?</span>\s*([^\s<]+)',
    re.I | re.S)


def _has(text: str, markers: tuple[str, ...]) -> str:
    """返回第一个命中的特征词 (小写匹配), 没命中返回空串."""
    for m in markers:
        if m in text:
            return m
    return ""


def classify_response(
    status: int,
    body: str = "",
    headers: dict[str, str] | None = None,
    final_url: str = "",
) -> Classification:
    """根据状态码 + 响应体 + 响应头判定 Cloudflare 归类.

    设计原则: 先看 CF 强特征 (封禁页), 再看盾; 两者都像时以封禁为准 ——
    因为封禁页里也可能引用 challenge 脚本, 但语义是"拒绝"而不是"验证".
    """
    h = {k.lower(): str(v) for k, v in (headers or {}).items()}
    text = (body or "")
    low = text[:200_000].lower()      # 只看前 200KB, 足够识别特征

    cf_server = "cloudflare" in h.get("server", "").lower()
    cf_ray = bool(h.get("cf-ray"))
    # 响应头层面能确认是 CF
    header_cf = cf_server or cf_ray or "cf-cache-status" in h
    # 正文层面能确认是 CF (页面里带 cloudflare 特征)
    body_cf = ("cloudflare" in low) or ("cf-error" in low) or ("cf_chl" in low)
    is_cf = header_cf or body_cf

    ray = ""
    m = _RAY_RE.search(text)
    if m:
        ray = m.group(1)
    elif h.get("cf-ray"):
        ray = h["cf-ray"]

    # ---- 1) CF 封禁页 (最高优先级) ----
    hit = _has(low, _CF_BLOCK_MARKERS)
    if hit:
        host = ""
        hm = _BLOCKED_HOST_RE.search(text)
        if hm:
            host = hm.group(1)
        title = ""
        tm = _TITLE_RE.search(text)
        if tm:
            title = " ".join(tm.group(1).split())[:60]
        return Classification(
            kind=CF_BLOCKED,
            reason=f"Cloudflare 拦截页 (特征: {hit}" +
                   (f", 标题: {title}" if title else "") + ")",
            is_cloudflare=True, ray_id=ray, blocked_host=host,
        )

    # ---- 2) CF 人机验证 / 盾 ----
    hit = _has(low, _CF_CHALLENGE_MARKERS)
    if hit:
        ctype = "turnstile" if "turnstile" in low else "js"
        return Classification(
            kind=CF_CHALLENGE,
            reason=f"Cloudflare 人机验证 (特征: {hit}) —— 浏览器可自动通过",
            is_cloudflare=True, ray_id=ray, challenge_type=ctype,
        )

    # ---- 3) CF 限流 ----
    if status == 429 or _has(low, _CF_RATE_MARKERS):
        hit = _has(low, _CF_RATE_MARKERS) or f"HTTP {status}"
        return Classification(
            kind=CF_RATE_LIMITED,
            reason=f"Cloudflare 限流 (特征: {hit})",
            is_cloudflare=is_cf, ray_id=ray,
        )

    # ---- 4) 成功 ----
    if 200 <= status < 300:
        return Classification(kind=SITE_OK, reason=f"HTTP {status}",
                              is_cloudflare=is_cf, ray_id=ray)

    # ---- 5) 5xx ----
    if status >= 500:
        return Classification(
            kind=CF_ERROR_5XX if is_cf else UNKNOWN_DENIED,
            reason=f"HTTP {status}" + (" (Cloudflare)" if is_cf else ""),
            is_cloudflare=is_cf, ray_id=ray,
        )

    # ---- 6) 4xx: 区分站点拒绝与未知 ----
    hit = _has(low, _SITE_DENY_MARKERS)
    if hit:
        return Classification(
            kind=SITE_DENIED,
            reason=f"HTTP {status}, 站点拒绝 (特征: {hit})",
            is_cloudflare=is_cf, ray_id=ray,
        )

    return Classification(
        kind=UNKNOWN_DENIED,
        reason=f"HTTP {status}, 未识别到 Cloudflare 特征"
               + (f", CF Ray {ray}" if ray else ""),
        is_cloudflare=is_cf, ray_id=ray,
    )
