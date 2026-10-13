"""结果数据仓库: 读取 results.json, 提供筛选/排序/导出.

把"什么样的节点算潜在可用"这条业务规则集中在这里, 让 CLI 和 Web 共用同一套定义,
避免两边判定不一致.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------- 可用性定义

# Gemini / Antigravity 的判定档位
GEMINI_ORDER = {"available": 0, "likely": 1, "unknown": 2, "blocked": 3, "unavailable": 4}

# 什么算"潜在可用" —— 这是导出的核心规则, 集中定义便于统一
#   1) 链路可达 (拿得到出口 IP)
#   2) Google 可访问
#   3) Gemini 没有被明确拒绝 (blocked / unavailable 都排除)
#      注: 当前版本已不再产生 "likely" 值 (地区改为黑名单制), 但保留它
#          是为了兼容早期生成的 results.json, 旧数据里 likely 代表可用.
USABLE_GEMINI = ("available", "likely")


def is_potentially_usable(row: dict[str, Any]) -> bool:
    """判断一条结果是否"潜在可用".

    若该条结果带有【分类体检】结论 (categories 字段非空), 则以分类结论为准 ——
    因为分类模式下后端只做了该分类的体检, Google 维度的字段可能没测 (全默认值),
    用旧的 Google 规则会判成不可用.
    但【链路可达】仍是必要条件: 连不上节点的, 分类结论也不该被采信.
    """
    cats = row.get("categories") or {}
    if isinstance(cats, dict) and cats:
        # 链路不可达 => 一律不可用 (分类探测可能也一并失败)
        if not row.get("reachable"):
            return False
        verds = [str((v or {}).get("verdict", "")) for v in cats.values()]
        return bool(verds) and all(v == "available" for v in verds)

    if not row.get("reachable"):
        return False
    if row.get("google") != "success":
        return False
    # 被 Google 判定为代理/VPN 出口 (hkredirect / sorry 风控) 的节点,
    # 即使页面返回 200, Gemini 与 Antigravity 实际也不可用, 必须排除.
    if str(row.get("risk_level", "none")) == "high":
        return False
    if str(row.get("gemini", "")) not in USABLE_GEMINI:
        return False
    return True


def category_verdict(row: dict[str, Any], key: str) -> str:
    """取某条结果在指定分类下的结论 (未检测返回空串)."""
    cats = row.get("categories") or {}
    if not isinstance(cats, dict):
        return ""
    c = cats.get(key)
    return str((c or {}).get("verdict", "")) if isinstance(c, dict) else ""


def row_categories(row: dict[str, Any]) -> list[str]:
    """该条结果实际检测过的分类列表."""
    cats = row.get("categories") or {}
    return sorted(cats.keys()) if isinstance(cats, dict) else []


def is_strictly_usable(row: dict[str, Any]) -> bool:
    """更严格的"综合可用"(与 CLI 的 ok 字段一致)."""
    return bool(row.get("ok"))


class ResultStore:
    """results.json 的读取与查询.

    数据量上限是几百~几千条, 直接放内存里反复筛选足够快;
    用锁保护重载, 避免测试进程正在写文件时读到半截内容.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._rows: list[dict[str, Any]] = []
        self._mtime: float = 0.0
        self._error: str = ""
        self.load()

    # ---------------------------------------------------------- 读取

    def load(self) -> None:
        """从磁盘重新载入. 文件不存在或损坏时保留空列表并记录错误."""
        with self._lock:
            self._error = ""
            if not self.path.is_file():
                self._rows = []
                self._error = f"结果文件不存在: {self.path.name}"
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8", errors="replace"))
            except json.JSONDecodeError as e:
                self._rows = []
                self._error = f"结果文件解析失败: {e}"
                return
            except OSError as e:
                self._rows = []
                self._error = f"读取失败: {e}"
                return

            if isinstance(raw, dict):
                raw = raw.get("results", [])
            if not isinstance(raw, list):
                self._rows = []
                self._error = "结果文件格式不是列表"
                return

            self._rows = [r for r in raw if isinstance(r, dict)]
            try:
                self._mtime = self.path.stat().st_mtime
            except OSError:
                self._mtime = 0.0

    def reload_if_changed(self) -> bool:
        """文件被改动过就重载 (测试跑完刷新页面即可看到新结果). 返回是否重载."""
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return False
        if mtime != self._mtime:
            self.load()
            return True
        return False

    @property
    def error(self) -> str:
        return self._error

    # ---------------------------------------------------------- 查询

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._rows)

    def query(
        self,
        search: str = "",
        protocol: str = "",
        country: str = "",
        gemini: str = "",
        antigravity: str = "",
        risk: str = "",
        category: str = "",
        cat_verdict: str = "",
        usable_only: bool = False,
        sort: str = "default",
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        """按条件筛选并排序.

        category:    只看检测过该分类的节点 (xai / google)
        cat_verdict: 该分类下的结论 (available / blocked / unavailable)
        """
        rows = self.all()

        if search:
            q = search.strip().lower()
            rows = [
                r for r in rows
                if q in str(r.get("tag", "")).lower()
                or q in str(r.get("server", "")).lower()
                or q in str(r.get("ip", "")).lower()
                or q in str(r.get("node", "")).lower()
            ]
        if protocol:
            rows = [r for r in rows if str(r.get("protocol", "")).upper() == protocol.upper()]
        if country:
            rows = [r for r in rows if str(r.get("country", "")).upper() == country.upper()]
        if gemini:
            rows = [r for r in rows if str(r.get("gemini", "")) == gemini]
        if antigravity:
            rows = [r for r in rows
                    if str(r.get("antigravity", "")) == antigravity]
        if risk:
            rows = [r for r in rows if str(r.get("risk_level", "none")) == risk]
        if category:
            rows = [r for r in rows if category in row_categories(r)]
            if cat_verdict:
                rows = [r for r in rows
                        if category_verdict(r, category) == cat_verdict]
        if usable_only:
            rows = [r for r in rows if is_potentially_usable(r)]

        rows.sort(key=lambda r: _sort_key(r, sort), reverse=(order == "desc"))
        return rows

    def summary(self) -> dict[str, Any]:
        """汇总统计, 供顶部指标卡使用."""
        rows = self.all()
        total = len(rows)

        def count(pred) -> int:
            return sum(1 for r in rows if pred(r))

        lats = [r.get("latency", 0) for r in rows
                if isinstance(r.get("latency"), (int, float))
                and r.get("latency", 0) > 0 and r.get("reachable")]
        rtts = [r.get("rtt_min_ms", 0) for r in rows
                if isinstance(r.get("rtt_min_ms"), (int, float))
                and r.get("rtt_min_ms", 0) > 0 and r.get("reachable")]
        ttfbs = [r.get("ttfb_ms", 0) for r in rows
                 if isinstance(r.get("ttfb_ms"), (int, float))
                 and r.get("ttfb_ms", 0) > 0 and r.get("reachable")]

        countries: dict[str, int] = {}
        protocols: dict[str, int] = {}
        for r in rows:
            cc = str(r.get("country", "") or "")
            if cc:
                countries[cc] = countries.get(cc, 0) + 1
            pt = str(r.get("protocol", "") or "")
            if pt:
                protocols[pt] = protocols.get(pt, 0) + 1

        return {
            "total": total,
            "reachable": count(lambda r: bool(r.get("reachable"))),
            "google_ok": count(lambda r: r.get("google") == "success"),
            "usable": count(is_potentially_usable),
            "strict_usable": count(is_strictly_usable),
            "gemini_available": count(lambda r: r.get("gemini") == "available"),
            "gemini_likely": count(lambda r: r.get("gemini") == "likely"),
            "gemini_blocked": count(lambda r: r.get("gemini") == "blocked"),
            "antigravity_available": count(lambda r: r.get("antigravity") == "available"),
            "risk_high": count(lambda r: str(r.get("risk_level", "none")) == "high"),
            "hk_redirect": count(lambda r: bool(r.get("hk_redirect"))),
            "categories": self._category_stats(rows),
            "avg_latency": int(sum(lats) / len(lats)) if lats else 0,
            "min_latency": int(min(lats)) if lats else 0,
            "avg_rtt": int(sum(rtts) / len(rtts)) if rtts else 0,
            "min_rtt": int(min(rtts)) if rtts else 0,
            "avg_ttfb": int(sum(ttfbs) / len(ttfbs)) if ttfbs else 0,
            "min_ttfb": int(min(ttfbs)) if ttfbs else 0,
            "countries": dict(sorted(countries.items(), key=lambda kv: -kv[1])),
            "protocols": dict(sorted(protocols.items(), key=lambda kv: -kv[1])),
            "error": self._error,
        }

    def _category_stats(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """按分类汇总: 各结论计数 + 各服务明细."""
        stats: dict[str, Any] = {}
        for r in rows:
            cats = r.get("categories") or {}
            if not isinstance(cats, dict):
                continue
            for key, cdata in cats.items():
                if not isinstance(cdata, dict):
                    continue
                b = stats.setdefault(key, {"available": 0, "blocked": 0,
                                           "unavailable": 0, "unknown": 0,
                                           "services": {}})
                b[str(cdata.get("verdict", "unknown"))] = \
                    b.get(str(cdata.get("verdict", "unknown")), 0) + 1
                for sk, sv in (cdata.get("services") or {}).items():
                    sb = b["services"].setdefault(sk, {})
                    sb[str(sv)] = sb.get(str(sv), 0) + 1
        return stats

    def facets(self) -> dict[str, list[dict[str, Any]]]:
        """给前端筛选下拉用的候选值 (带计数)."""
        rows = self.all()
        countries: dict[str, int] = {}
        protocols: dict[str, int] = {}
        geminis: dict[str, int] = {}
        antigravities: dict[str, int] = {}
        risks: dict[str, int] = {}
        categories: dict[str, int] = {}
        for r in rows:
            for key, bucket in (("country", countries), ("protocol", protocols),
                                ("gemini", geminis),
                                ("antigravity", antigravities)):
                v = str(r.get(key, "") or "")
                if v:
                    bucket[v] = bucket.get(v, 0) + 1
            risks[str(r.get("risk_level", "none") or "none")] = \
                risks.get(str(r.get("risk_level", "none") or "none"), 0) + 1
            for ck in row_categories(r):
                categories[ck] = categories.get(ck, 0) + 1
        return {
            "countries": _as_facet(countries),
            "protocols": _as_facet(protocols),
            "gemini": _as_facet(geminis),
            "antigravity": _as_facet(antigravities),
            "risk": _as_facet(risks),
            "categories": _as_facet(categories),
        }

    def export_uris(self, scope: str = "usable", ids: Iterable[str] | None = None) -> list[str]:
        """导出原始 URI 列表.

        scope:
          usable  —— 潜在可用 (默认, 用户要求的行为)
          strict  —— 仅综合可用 (ok=true)
          all     —— 全部节点
        ids: 指定则只导出这些 node_id (前端的勾选导出).
        """
        rows = self.all()
        if ids is not None:
            want = set(ids)
            rows = [r for r in rows if str(r.get("node_id", "")) in want]
        elif scope == "usable":
            rows = [r for r in rows if is_potentially_usable(r)]
        elif scope == "strict":
            rows = [r for r in rows if is_strictly_usable(r)]
        elif scope == "all":
            pass
        else:
            rows = [r for r in rows if is_potentially_usable(r)]

        # 按"好用程度"排序, 让导出的第一行就是最优节点
        rows.sort(key=lambda r: _sort_key(r, "default"))

        out: list[str] = []
        seen: set[str] = set()
        for r in rows:
            uri = str(r.get("node", "")).strip()
            if not uri or uri in seen:
                continue
            seen.add(uri)
            out.append(uri)
        return out


# ---------------------------------------------------------------- 排序


def _sort_key(row: dict[str, Any], sort: str):
    """生成排序键. 缺值一律排到最后, 避免 None 参与比较报错."""
    BIG = 10**9

    if sort == "rtt":
        # 链路往返 (小请求最小值) —— 最干净的快慢指标, 推荐默认用它比较
        v = row.get("rtt_min_ms", 0)
        if not isinstance(v, (int, float)) or v <= 0 or not row.get("reachable"):
            return BIG
        return v
    if sort == "ttfb":
        # google.com 首字节 (不含 body 下载)
        v = row.get("ttfb_ms", 0)
        if not isinstance(v, (int, float)) or v <= 0 or not row.get("reachable"):
            return BIG
        return v
    if sort == "latency":
        # 延迟排序要排除两类噪声:
        #   1) 未取到延迟 (0/None)
        #   2) 链路不可达的节点 —— 例如"剩余流量"这类信息节点响应极快(个位数毫秒),
        #      若不排除会把它们排到最前面, 看上去像"最快的节点".
        # 注意: 这是"端到端含 body"的旧口径, 混入了下载 ~50KB 首页的时间,
        #       横向比快慢请用 rtt / ttfb.
        v = row.get("latency", 0)
        if not isinstance(v, (int, float)) or v <= 0 or not row.get("reachable"):
            return BIG
        return v
    if sort == "country":
        return (str(row.get("country", "") or "ZZ"),)
    if sort == "protocol":
        return (str(row.get("protocol", "") or "ZZ"),)
    if sort == "tag":
        return (str(row.get("tag", "") or ""),)
    if sort == "ip":
        return (str(row.get("ip", "") or "~"),)
    # default: 可用优先 -> Gemini 档位 -> 链路快慢(用 rtt, 缺失回退 latency)
    speed = row.get("rtt_min_ms", 0) or row.get("latency", 0)
    if not isinstance(speed, (int, float)) or speed <= 0:
        speed = BIG
    return (
        0 if is_potentially_usable(row) else 1,
        0 if row.get("google") == "success" else 1,
        GEMINI_ORDER.get(str(row.get("gemini", "")), 9),
        speed,
    )


def _as_facet(counter: dict[str, int]) -> list[dict[str, Any]]:
    return [{"value": k, "count": v}
            for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]
