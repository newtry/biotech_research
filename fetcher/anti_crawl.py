"""
Anti-crawl Utilities
反爬基础工具集

设计原则（参见 TECH_SPEC.md §6.2）：
- L1 默认：拟人化请求头 + 随机 delay + ETag 缓存 + 指数退避
- L2 中等：补充真实 Referer / cookie 复用
- L3 重度：降级到 Playwright（lazy import，按需启用）

不持有任何状态，pure functions + dataclass。
"""
import random
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


# ---- UA 池 ----
# 5 个 2024-2026 主流桌面 UA，跨浏览器/平台。每请求随机抽一个
USER_AGENTS: Tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)


# ---- 重试配置 ----

@dataclass
class RetryConfig:
    """重试策略：指数退避 + 抖动"""
    max_retries: int = 3            # 总尝试次数（含首次）
    initial_delay: float = 1.0      # 首次重试前 sleep 秒数
    backoff_factor: float = 2.0     # 每次重试 delay 倍率
    max_delay: float = 30.0         # 单次 sleep 上限
    jitter: float = 0.2             # 抖动比例（实际 sleep ∈ delay*(1±jitter)）


# ---- ETag / Last-Modified 缓存 ----

class RequestCache:
    """
    进程内 ETag / Last-Modified 缓存。
    同一 URL 二次请求时自动带 If-None-Match / If-Modified-Since，
    命中 304 后请求体为空，省带宽也减回源次数。
    """
    _KEEP_HEADERS = ("etag", "last-modified")

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, str]] = {}

    def get_headers(self, url: str) -> Dict[str, str]:
        entry = self._store.get(url)
        if not entry:
            return {}
        return {k: v for k, v in entry.items() if k.lower() in self._KEEP_HEADERS}

    def store(self, url: str, response_headers: Dict[str, str]) -> None:
        keep = {
            k: v for k, v in response_headers.items()
            if k.lower() in self._KEEP_HEADERS
        }
        if keep:
            self._store[url] = keep

    def clear(self) -> None:
        self._store.clear()


# ---- 拟人化工具 ----

def random_delay(min_s: float = 1.0, max_s: float = 3.0) -> float:
    """sleep 一段随机时长，返回实际 sleep 秒数。"""
    delay = random.uniform(min_s, max_s)
    time.sleep(delay)
    return delay


def humanize_headers(
    url: str,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
    extra: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    生成"看起来像浏览器"的请求头。
    - User-Agent 随机从 UA 池抽
    - Accept / Accept-Language / Accept-Encoding 写齐
    - Referer 默认取 url 的 origin（避免空 Referer 暴露爬虫）
    """
    if not user_agent:
        user_agent = random.choice(USER_AGENTS)

    if not referer:
        # origin = scheme + host
        parts = url.split("/", 3)
        if len(parts) >= 3:
            referer = f"{parts[0]}//{parts[2]}/"

    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json, text/html, application/xhtml+xml, */*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    if referer:
        headers["Referer"] = referer
    if extra:
        headers.update(extra)
    return headers


# ---- 反爬检测 ----

# 触发 L3 降级的反爬特征（响应头/正文片段）
_ANTICRAWL_BODY_SIGNATURES = (
    "访问频率过快",
    "请输入验证码",
    "captcha",
    "robot check",
    "robot.txt",
    "access denied",
    "forbidden",
    "请开启JavaScript",
    "enable javascript",
)


def is_blocked(status_code: int, body: str) -> bool:
    """
    启发式判断：服务器是否在挡我们。
    - 403/429/503 视为明确阻挡
    - 响应正文含已知反爬特征（如"请输入验证码"）也视为阻挡
    """
    if status_code in (403, 429, 503):
        return True
    # 只扫前 5KB 避免大响应拖累
    snippet = body[:5000].lower()
    return any(sig in snippet for sig in _ANTICRAWL_BODY_SIGNATURES)
