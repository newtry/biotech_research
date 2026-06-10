"""
BaseFetcher — 所有 fetcher 的共享基类

提供：
- Session 复用（自动维持 cookie）
- 拟人化请求头（UA 池轮换）
- 请求间随机 delay（避免被识别为机器人）
- 指数退避重试（429 / 5xx 触发）
- ETag 缓存（GET 304 短路）
- 反爬检测 → L3 浏览器降级入口（lazy Playwright）

子类只需要实现具体的业务方法（get_xxx_data），
HTTP 全部走 self.get / self.post。
"""
import logging
import random
import time
from typing import Optional, Dict, Any, Union

import requests

from .anti_crawl import (
    RequestCache,
    RetryConfig,
    humanize_headers,
    is_blocked,
    random_delay,
)

logger = logging.getLogger(__name__)


class BaseFetcher:
    """
    反爬友好的 HTTP fetcher 基类

    Args:
        retry: 重试配置，None 用默认 (3 次 + 指数退避)
        delay_range: 成功请求后 sleep 区间 (min, max)，None 用默认 (1, 3) 秒
        use_cache: 是否启用 ETag 缓存（GET）
    """

    DEFAULT_RETRY = RetryConfig()
    DEFAULT_DELAY: tuple = (1.0, 3.0)
    REQUEST_TIMEOUT = 30

    def __init__(
        self,
        retry: Optional[RetryConfig] = None,
        delay_range: Optional[tuple] = None,
        use_cache: bool = True,
    ) -> None:
        self.retry = retry or self.DEFAULT_RETRY
        self.delay_range = delay_range or self.DEFAULT_DELAY
        self.session = requests.Session()
        self.cache: Optional[RequestCache] = RequestCache() if use_cache else None

        # L3 浏览器 lazy 句柄（不导入 playwright，避免强依赖）
        self._playwright = None
        self._browser = None

    # ---- 公共 API ----

    def get(self, url: str, params: Optional[Dict] = None, **kwargs) -> requests.Response:
        return self._request("GET", url, params=params, **kwargs)

    def post(
        self,
        url: str,
        json: Optional[Dict] = None,
        data: Optional[Dict] = None,
        **kwargs,
    ) -> requests.Response:
        return self._request("POST", url, json=json, data=data, **kwargs)

    # ---- 核心请求循环 ----

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None,
        data: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        **kwargs,
    ) -> requests.Response:
        """
        统一请求入口：
        1. 拟人化 header（含 ETag 缓存头）
        2. 失败重试：429 / 5xx 触发指数退避
        3. 反爬检测 → 尝试 L3 浏览器降级
        4. 成功后 sleep 拟人化（仅首次成功后）
        """
        merged_headers = humanize_headers(url)
        if headers:
            merged_headers.update(headers)
        if method == "GET" and self.cache:
            merged_headers.update(self.cache.get_headers(url))

        timeout = timeout or self.REQUEST_TIMEOUT
        delay = self.retry.initial_delay
        last_resp: Optional[requests.Response] = None
        last_exc: Optional[Exception] = None

        for attempt in range(self.retry.max_retries):
            try:
                if attempt > 0:
                    jitter = random.uniform(0, delay * self.retry.jitter)
                    time.sleep(delay + jitter)
                    delay = min(delay * self.retry.backoff_factor, self.retry.max_delay)

                resp = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json,
                    data=data,
                    headers=merged_headers,
                    timeout=timeout,
                    **kwargs,
                )
                last_resp = resp

                # 304 Not Modified：视为成功空响应
                if resp.status_code == 304:
                    logger.debug("Cache hit 304: %s", url)
                    return resp

                # 触发重试的状态码
                if resp.status_code == 429 or resp.status_code >= 500:
                    logger.warning(
                        "[%s %s] status=%d, retry %d/%d",
                        method, url, resp.status_code, attempt + 1, self.retry.max_retries,
                    )
                    last_exc = requests.HTTPError(f"{resp.status_code}")
                    continue

                # 反爬检测 → 尝试 L3 浏览器降级
                if is_blocked(resp.status_code, resp.text):
                    logger.warning(
                        "[%s %s] suspected anti-crawl block, attempting L3 browser fallback",
                        method, url,
                    )
                    browser_resp = self._fetch_via_browser(url)
                    if browser_resp is not None:
                        return browser_resp
                    # 浏览器也没辙，继续走重试
                    last_exc = requests.HTTPError(f"anti-crawl block (status={resp.status_code})")
                    continue

                resp.raise_for_status()

                # 缓存 200 的 ETag / Last-Modified
                if method == "GET" and self.cache and resp.status_code == 200:
                    self.cache.store(url, dict(resp.headers))

                # 首次成功 → 拟人化 sleep
                if attempt == 0:
                    random_delay(*self.delay_range)

                return resp

            except requests.RequestException as e:
                last_exc = e
                logger.warning(
                    "[%s %s] %s, retry %d/%d",
                    method, url, e, attempt + 1, self.retry.max_retries,
                )

        # 所有重试都耗尽
        logger.error("[%s %s] all %d retries exhausted", method, url, self.retry.max_retries)
        if last_resp is not None:
            # 返回最后一次响应，让调用方检查 status_code
            return last_resp
        raise last_exc or requests.RequestException(f"All retries failed for {url}")

    # ---- L3 浏览器降级（lazy Playwright）----

    def _fetch_via_browser(self, url: str) -> Optional[requests.Response]:
        """
        L3 降级：用 Playwright 渲染后回灌 cookie 到 self.session，
        然后用普通请求重试同一 URL。
        Playwright 缺包时返回 None（不抛错），由 _request 走重试逻辑。

        子类可重写 _extract_via_browser(url) 拿到渲染后的 HTML 自己解析
        （CDE 这种 JS 加载数据的不适用于本默认实现）
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning(
                "Playwright not installed. L3 fallback skipped. "
                "Install with: pip install playwright && playwright install chromium"
            )
            return None

        try:
            if self._browser is None:
                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(headless=True)

            context = self._browser.new_context(
                user_agent=random.choice([
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ]),
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                # 把渲染后拿到的 cookie 回灌到 requests.Session
                for c in context.cookies():
                    self.session.cookies.set(
                        c["name"], c["value"], domain=c.get("domain"),
                    )
            finally:
                page.close()
                context.close()

            # 用回灌的 cookie 再发一次普通请求
            return self.session.get(url, headers=humanize_headers(url), timeout=self.REQUEST_TIMEOUT)

        except Exception as e:
            logger.error("L3 browser fallback failed for %s: %s", url, e)
            return None

    def close(self) -> None:
        """清理资源（浏览器句柄、session）"""
        try:
            self.session.close()
        except Exception:
            pass
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
