"""
Stock Announcements Fetcher
从交易所获取上市公司公告，重点关注医药公司管线进展
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import json

from .base import BaseFetcher


class AnnouncementFetcher(BaseFetcher):
    """上市公司公告获取

    当前实现：东方财富医药行业公告 API（关键词过滤）。
    上交所 / 深交所 / 港交所的方法占位，真实接入待做。
    """

    # A股交易所披露
    SSE_URL = "http://www.sse.com.cn/disclosure/listedinfo/announcement/"
    SZSE_URL = "http://www.szse.cn/disclosure/listed/"

    # 港交所披露
    HKEX_NEWS_URL = "https://www.hkex.com.hk/News/NewsAnnouncements?lang=zh-CN"

    # 东方财富医药行业公告
    EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

    def get_sse_announcements(self, date: str = None, page: int = 1) -> List[Dict]:
        """
        获取上交所公告
        """
        results = []
        url = f"{self.SSE_URL}announcedetail? announcements_date={date}&page={page}"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }

        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            # 解析 HTML 公告列表
        except Exception as e:
            print(f"SSE API error: {e}")

        return results

    def get_szse_announcements(self, date: str = None) -> List[Dict]:
        """
        获取深交所公告
        """
        results = []
        url = f"{self.SZSE_URL}? announcements_date={date}"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }

        try:
            resp = requests.get(url, headers=headers, timeout=30)
        except Exception as e:
            print(f"SZSE API error: {e}")

        return results

    def get_hkex_announcements(self, stock_code: str = None, page: int = 1) -> List[Dict]:
        """
        获取港交所公告
        """
        results = []

        # 港交所新闻公告
        url = f"https://www.hkex.com.hk/News/NewsAnnouncements?lang=zh-CN&cat=Latest"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }

        try:
            resp = requests.get(url, headers=headers, timeout=30)
            soup = BeautifulSoup(resp.text, 'lxml')

            # 解析公告列表
            items = soup.find_all('div', class_='news-item')
            for item in items[:30]:
                title_elem = item.find('a')
                if title_elem:
                    results.append({
                        'title': title_elem.get_text(strip=True),
                        'url': title_elem.get('href', ''),
                        'date': item.find('span', class_='date').get_text(strip=True) if item.find('span', class_='date') else '',
                        'source': 'HKEX'
                    })
        except Exception as e:
            print(f"HKEX API error: {e}")

        return results

    def search_pharma_announcements(self, keywords: Optional[List[str]] = None, date_from: str = None, date_to: str = None) -> List[Dict]:
        """
        搜索医药相关公告

        Args:
            keywords: 关键词列表，如 ["临床试验", "获批", "III期", "NDA"]
            date_from: 开始日期
            date_to: 结束日期
        """
        results = []

        # 东方财富医药行业公告
        url = self.EASTMONEY_URL
        params = {
            'reportName': 'RPT_ANNOUN_TAB',
            'columns': 'ALL',
            'filter': '(SECURITY_TYPE_CODE="05800101")',
            'pageNumber': 1,
            'pageSize': 50,
            'sortColumns': 'PUBLISH_DATE',
            'sortTypes': -1
        }

        # Referer 必须从 .eastmoney.com 起，否则 403
        extra_headers = {
            'Referer': 'https://data.eastmoney.com/',
        }

        if date_from:
            params['filter'] += f',(PUBLISH_DATE>=\"{date_from}\")'
        if date_to:
            params['filter'] += f',(PUBLISH_DATE<=\"{date_to}\")'

        try:
            resp = self.get(url, params=params, headers=extra_headers)
            resp.raise_for_status()
            data = resp.json()

            if data.get('success'):
                items = data.get('result', {}).get('data', [])
                for item in items:
                    title = item.get('TITLE', '')
                    # 按关键词过滤
                    if keywords:
                        if not any(kw in title for kw in keywords):
                            continue

                    results.append({
                        'title': title,
                        'stock_code': item.get('SECURITY_CODE', ''),
                        'stock_name': item.get('SECURITY_NAME_ABBR', ''),
                        'publish_date': item.get('PUBLISH_DATE', ''),
                        'announcement_type': item.get('NOTICE_TYPE', ''),
                        'source': 'Eastmoney A-share'
                    })
        except Exception as e:
            print(f"Eastmoney API error: {e}")

        return results


if __name__ == "__main__":
    fetcher = AnnouncementFetcher()

    # 测试：搜索最近的医药相关公告
    keywords = ["临床试验", "III期", "NDA", "BLA", "获批", "临床结果"]
    date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    announcements = fetcher.search_pharma_announcements(keywords=keywords, date_from=date_from)
    print(f"Found {len(announcements)} pharma announcements")
    for a in announcements[:10]:
        print(f"  [{a['stock_code']}] {a['title']} - {a['publish_date']}")