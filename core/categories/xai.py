"""XAI (xAI / Grok) 分类体检.

检测项: Grok Web (grok.com)

判定要点 (来自需求与实测):
  grok.com 由 Cloudflare 保护, 返回 403 时**必须看内容**:
    - CF 封禁页 (cf-error-details / "Sorry, you have been blocked")
      => 该出口被 WAF 拒绝, 真实浏览器同样进不去 => 不可用
    - CF 人机验证/盾 (challenge-platform / Turnstile / Just a moment)
      => 只是验证真人, 浏览器会自动通过 => 可用
  只看状态码会把这两种情况混为一谈, 这正是本分类要解决的问题。

地区: xAI/Grok 目前不支持的地区主要是 CN/HK/MO 等, 但**不单独按地区否决** ——
      以实际响应为准 (同 GOOGLE 分类的经验: 地区与可达性并不等价)。
"""

from __future__ import annotations

from typing import Any

from ..cfclass import (CF_CHALLENGE, CF_BLOCKED, classify_response)
from .base import Category, CategoryResult, Service, register

# Grok Web 入口
GROK_URL = "https://grok.com/"

# 正常页面特征 (用于确认拿到的确实是 Grok 应用外壳而不是别的页面)
_GROK_MARKERS = ("grok", "x.ai", "xai")


def _judge_grok(status: int, body: str, headers: dict[str, str],
                final_url: str) -> tuple[str, str]:
    """判定 grok.com 的可用性.

    返回 (verdict, reason). verdict: available / blocked / unavailable
    """
    if status == 0:
        return "unavailable", "请求失败"

    cls = classify_response(status, body, headers, final_url)

    # --- CF 封禁: 明确不可用 ---
    if cls.kind == CF_BLOCKED:
        return "blocked", (f"Cloudflare 封禁该出口 ({cls.reason})"
                           + (f", Ray {cls.ray_id}" if cls.ray_id else ""))

    # --- CF 盾: 可用 (浏览器能过) ---
    if cls.kind == CF_CHALLENGE:
        kind = "Turnstile" if cls.challenge_type == "turnstile" else "JS 挑战"
        return "available", f"通过 Cloudflare {kind} 层 (浏览器可自动完成)"

    # --- 限流 / 5xx ---
    if cls.kind in ("cf_rate_limited", "cf_error"):
        return "unavailable", cls.reason

    # --- 正常 2xx: 确认页面内容像 Grok ---
    if 200 <= status < 300:
        low = body[:20_000].lower()
        if any(m in low for m in _GROK_MARKERS):
            return "available", f"HTTP {status}, 页面可达"
        # 200 但内容不像 Grok: 可能是被劫持/落地页, 保守判可用但说明情况
        return "available", f"HTTP {status}, 页面可达 (未识别到 Grok 特征)"

    # --- 其它 4xx (如 401/404): 站点可达但拒绝访问 ---
    if 400 <= status < 500:
        if cls.kind == "site_denied":
            # 需要登录之类, 说明站点本身可达 —— 节点没问题
            return "available", f"HTTP {status}, 站点可达 ({cls.reason})"
        return "unavailable", cls.reason

    return "unavailable", cls.reason or f"HTTP {status}"


class XaiCategory(Category):
    key = "xai"
    label = "XAI"
    services = (
        Service("grok", "Grok Web", GROK_URL, _judge_grok),
    )

    def probe(self, proxy_url: str) -> CategoryResult:
        res = CategoryResult(category=self.key)
        svc = self.services[0]

        with self.client(proxy_url) as c:
            r = self.fetch(c, svc.url)

        verdict, reason = "unavailable", "请求失败"
        cls_kind = ""
        if r["error"]:
            reason = f"请求失败: {r['error']}"
        else:
            verdict, reason = svc.judge(
                r["status"], r["body"], r["headers"], r["final_url"])
            cls = classify_response(r["status"], r["body"], r["headers"],
                                    r["final_url"])
            cls_kind = cls.kind
            res.detail["cf_ray"] = cls.ray_id
            res.detail["cf_kind"] = cls.kind
            if cls.blocked_host:
                res.detail["blocked_host"] = cls.blocked_host

        res.services[svc.key] = verdict
        res.reasons[svc.key] = reason
        res.detail["http_status"] = r["status"]
        res.detail["final_url"] = r["final_url"][:200]
        res.probes.append({
            "name": svc.key, "status": r["status"],
            "final_url": r["final_url"][:200], "error": r["error"],
            "cf_kind": cls_kind,
        })

        # 分类级结论: 以 grok 为准 (将来有更多服务时取综合)
        res.verdict = verdict
        res.reason = reason
        return res

# 注册 (放在类定义之后, 避免前向引用)
register(lambda timeout: XaiCategory(timeout))
