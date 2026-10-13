"""分类体检框架.

设计目标 (按需求):
  - 节点按用途分成若干【大类】(当前: XAI / GOOGLE), 后续可继续加
  - 一个分类【只检测自己分类的项】: 测 GOOGLE 时不碰 grok, 反之亦然
  - 后端只做该分类对应的体检, 不做额外的事

扩展方式: 继承 Category 并实现 probe(), 再注册进 REGISTRY 即可 ——
无需改动 tester / store / web 的代码。

每个 Category 的产物是一组 (字段名 -> 值) 的键值对, 由 build_result() 汇成
统一的结果字典; 这样不同分类可以有自己的判定维度, 而存储与展示层保持通用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import httpx


# ---------------------------------------------------------------- 服务条目

@dataclass
class Service:
    """分类下的一个具体检测项 (例如 GOOGLE 分类里的 gemini)."""

    key: str                      # 结果字段名, 如 "gemini"
    label: str                    # 展示名
    url: str                      # 检测地址
    # 判定函数: (status, body, headers, final_url) -> ( verdict, reason )
    # verdict 取值: available / blocked / unavailable
    judge: Callable[..., tuple[str, str]]


# ---------------------------------------------------------------- 分类基类

@dataclass
class CategoryResult:
    """一个分类在某节点上的体检结果."""

    category: str = ""
    verdict: str = "unavailable"      # 分类级综合结论
    reason: str = ""
    services: dict[str, str] = field(default_factory=dict)      # key -> verdict
    reasons: dict[str, str] = field(default_factory=dict)       # key -> reason
    detail: dict[str, Any] = field(default_factory=dict)        # 其它指标
    probes: list[dict[str, Any]] = field(default_factory=list)  # 原始探测记录

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "verdict": self.verdict,
            "reason": self.reason,
            "services": self.services,
            "reasons": self.reasons,
            "detail": self.detail,
        }


class Category:
    """分类体检的基类.

    子类需要提供:
      key         分类标识 (xai / google), 用于结果字段与筛选
      label       展示名
      services    该分类要检测的服务列表
      probe()     实现具体探测与判定
    """

    key: str = ""
    label: str = ""
    services: tuple[Service, ...] = ()

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    # -------------------------------------------------- 工具

    def client(self, proxy_url: str) -> httpx.Client:
        """按分类定制的 HTTP 客户端.

        trust_env=False 必须保留: 本机 v2rayN 设置了系统代理, 若不关闭,
        请求会走 v2rayN 出口而不是被测节点, 导致全部误判 (早期踩过).
        """
        # 注意: 本模块在 core.categories 包内, 引用上层要用 ..detector
        from ..detector import _HEADERS  # 复用同一套浏览器请求头

        return httpx.Client(
            proxy=proxy_url,
            trust_env=False,
            timeout=httpx.Timeout(self.timeout),
            headers=_HEADERS,
            follow_redirects=True,
            verify=False,
        )

    @staticmethod
    def fetch(client: httpx.Client, url: str) -> dict[str, Any]:
        """取一次响应 (含 body), 供各分类共用."""
        out: dict[str, Any] = {"url": url, "status": 0, "body": "",
                               "headers": {}, "final_url": "", "error": ""}
        try:
            r = client.get(url)
            out["status"] = r.status_code
            out["final_url"] = str(r.url)
            out["headers"] = dict(r.headers)
            # body 截断保存: CF 特征都在前部, 无需整页 (grok 页约 6KB)
            out["body"] = r.text[:300_000]
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        return out

    # -------------------------------------------------- 子类实现

    def probe(self, proxy_url: str) -> CategoryResult:
        raise NotImplementedError

    # -------------------------------------------------- 统一入口

    def run(self, proxy_url: str) -> CategoryResult:
        """执行本分类的体检. 异常不外抛, 收敛成 unavailable."""
        try:
            res = self.probe(proxy_url)
        except Exception as e:
            res = CategoryResult(
                category=self.key,
                verdict="unavailable",
                reason=f"体检异常: {type(e).__name__}: {str(e)[:120]}",
            )
        res.category = self.key
        return res


# ---------------------------------------------------------------- 注册表

REGISTRY: dict[str, Callable[[float], Category]] = {}


def register(factory: Callable[[float], Category]) -> Callable[[float], Category]:
    """把一个分类工厂注册进全局表."""
    inst = factory(1.0)
    REGISTRY[inst.key] = factory
    return factory


def build(key: str, timeout: float = 15.0) -> Category:
    """按 key 构造分类体检器."""
    if key not in REGISTRY:
        raise KeyError(f"未知分类: {key}")
    return REGISTRY[key](timeout)


def all_categories() -> dict[str, str]:
    """返回 {key: label}, 供前端下拉与 CLI 参数校验."""
    return {k: f(1.0).label for k, f in REGISTRY.items()}
