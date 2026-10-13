"""GOOGLE 分类体检 (Google / Gemini / Antigravity).

这是原有能力的归类封装 —— 判定逻辑与 core/detector.py 保持一致,
只是收进分类框架, 以便:
  - 与 XAI 分类并列, 互不干扰
  - 测 GOOGLE 时不会去请求 grok.com (反之亦然)

检测项:
  google       —— google.com 可达性 + 重定向风险 (核心判据: 是否跳 com.hk)
  gemini       —— Gemini 网页版
  antigravity  —— 跟随 Gemini, 但地区口径更严 (HK/MO 不支持)

复用 detector._analyze_risk 的思路, 但这里按分类职责重新组织,
避免把 XAI 无关的逻辑混进来。
"""

from __future__ import annotations

from typing import Any

from ..cfclass import classify_response
from ..detector import region_status
from .base import Category, CategoryResult, Service, register

GOOGLE_URL = "https://www.google.com/"
GEMINI_URL = "https://gemini.google.com/"
GEMINI_APP_URL = "https://gemini.google.com/app"

# 跳转风险词 (与 detector 一致)
_RISK_KEYWORDS = ("hkredirect", "sorry", "captcha", "unusual", "com.hk", "com.cn")


def _judge_google(status: int, body: str, headers: dict[str, str],
                  final_url: str) -> tuple[str, str]:
    if status == 0:
        return "unavailable", "请求失败"
    if 200 <= status < 300:
        return "available", f"HTTP {status}"
    cls = classify_response(status, body, headers, final_url)
    if cls.kind == "cf_challenge":
        return "available", "通过 Cloudflare 验证层"
    return "unavailable", cls.reason or f"HTTP {status}"


def _judge_gemini(status: int, body: str, headers: dict[str, str],
                  final_url: str) -> tuple[str, str]:
    if status == 0:
        return "unavailable", "请求失败"
    cls = classify_response(status, body, headers, final_url)
    if cls.kind == "cf_blocked":
        return "blocked", cls.reason
    if cls.kind == "cf_challenge":
        return "available", "通过 Cloudflare 验证层"
    if 200 <= status < 300:
        if "gemini.google.com" not in final_url:
            return "unavailable", f"被重定向到 {final_url[:70]}"
        return "available", f"HTTP {status}, 页面可达"
    return "unavailable", cls.reason or f"HTTP {status}"


def _judge_antigravity(status: int, body: str, headers: dict[str, str],
                       final_url: str) -> tuple[str, str]:
    if status == 0:
        return "unavailable", "请求失败"
    cls = classify_response(status, body, headers, final_url)
    if cls.kind in ("cf_blocked", "cf_rate_limited"):
        return "blocked", cls.reason
    if cls.kind == "cf_challenge":
        return "available", "通过 Cloudflare 验证层"
    if 200 <= status < 300:
        if "antigravity.google" not in final_url:
            return "unavailable", f"被重定向到 {final_url[:70]}"
        return "available", f"HTTP {status}, 页面可达"
    return "unavailable", cls.reason or f"HTTP {status}"


class GoogleCategory(Category):
    key = "google"
    label = "GOOGLE"
    services = (
        Service("google", "Google", GOOGLE_URL, _judge_google),
        Service("gemini", "Gemini 网页版", GEMINI_URL, _judge_gemini),
        Service("antigravity", "Antigravity", GEMINI_APP_URL, _judge_antigravity),
    )

    def probe(self, proxy_url: str) -> CategoryResult:
        res = CategoryResult(category=self.key)
        cc = ""

        with self.client(proxy_url) as c:
            # 1) google.com —— 可达性 + 重定向风险 (核心判据)
            g = self.fetch(c, GOOGLE_URL)
            final = (g["final_url"] or "").lower()
            flags = [k for k in _RISK_KEYWORDS if k in final]
            hk_redirect = ("google.com.hk" in final) or ("hkredirect" in final)

            gcls = classify_response(g["status"], g["body"], g["headers"],
                                     g["final_url"])
            if g["error"]:
                gv, gr = "unavailable", f"请求失败: {g['error']}"
            else:
                gv, gr = _judge_google(g["status"], g["body"], g["headers"],
                                       g["final_url"])
            # 跳转到 com.hk => 该出口被判定为代理, Google 侧不可用
            if hk_redirect:
                gv = "blocked"
                gr = "访问 www.google.com 被重定向到 www.google.com.hk"

            res.services["google"] = gv
            res.reasons["google"] = gr
            res.detail["google_final_url"] = g["final_url"][:200]
            res.detail["google_status"] = g["status"]
            res.detail["hk_redirect"] = hk_redirect
            res.detail["risk_flags"] = flags
            res.probes.append({"name": "google", "status": g["status"],
                               "final_url": g["final_url"][:200],
                               "error": g["error"], "cf_kind": gcls.kind})

            # 2) 出口地区 (来自 cf trace, 与 detector 一致)
            t = self.fetch(c, "https://www.cloudflare.com/cdn-cgi/trace")
            if t["status"] == 200:
                kv = {}
                for line in t["body"].splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        kv[k.strip()] = v.strip()
                res.detail["ip"] = kv.get("ip", "")
                cc = (kv.get("loc", "") or "").upper()
                res.detail["country"] = cc

            # 3) Gemini 网页版
            m = self.fetch(c, GEMINI_URL)
            if m["error"]:
                mv, mr = "unavailable", f"请求失败: {m['error']}"
            else:
                mv, mr = _judge_gemini(m["status"], m["body"], m["headers"],
                                       m["final_url"])
            # 地区: 用 web 口径 (HK/MO 网页版可用, 不算不支持)
            bad, why = region_status(cc, "web")
            if bad and mv == "available":
                mv, mr = "blocked", why
            # 风险重定向否决
            if hk_redirect and mv in ("available", "likely"):
                mv, mr = "blocked", f"风险重定向: {gr}"
            res.services["gemini"] = mv
            res.reasons["gemini"] = mr

            # 4) Antigravity: 跟随 Gemini, 但地区用更严的 api 口径
            ag_bad, ag_why = region_status(cc, "api")
            if ag_bad:
                av, ar = "blocked", f"{ag_why} (Gemini 网页版仍可用)"
            elif mv == "available":
                av, ar = "available", "跟随 Gemini (Google 与 Gemini 均可用)"
            elif mv == "blocked":
                av, ar = "blocked", f"跟随 Gemini: {mr}"
            else:
                av, ar = "unavailable", f"跟随 Gemini: {mr}"
            res.services["antigravity"] = av
            res.reasons["antigravity"] = ar

        # ---- 分类级结论: Google 通 + Gemini 可用 => 可用
        if gv == "available" and res.services["gemini"] == "available":
            res.verdict, res.reason = "available", "Google 与 Gemini 均可用"
        elif res.services["gemini"] == "blocked":
            res.verdict, res.reason = "blocked", res.reasons["gemini"]
        else:
            res.verdict, res.reason = "unavailable", res.reasons["gemini"] or gr
        return res

# 注册 (放在类定义之后, 避免前向引用)
register(lambda timeout: GoogleCategory(timeout))
