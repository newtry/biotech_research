"""
FDA Drug Approvals Fetcher
从 openFDA drugsfda 端点获取 NDA/BLA 审批数据

API 关键事实（实测）：
- 端点：https://api.fda.gov/drug/drugsfda.json
- 限速：240 req/min/IP（匿名），免费 key 提升到 240 req/min 不限日
- 关键字段：
  - application_number: NDA/BLA/ANDA 前缀
  - sponsor_name
  - products[].brand_name / active_ingredients[].name
  - submissions[]: 含 type/status/status_date，找 type=ORIG + status=AP 取首次获批日
- 坑：嵌套搜索不会强制同元素匹配，需要客户端二次过滤
  （如 2020 ORIG + 2026 SUPPL 的药，会被"日期范围 + ORIG"误命中）
"""
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from .base import BaseFetcher


class FDAFetcher(BaseFetcher):
    """FDA 审批数据获取（openFDA drugsfda）

    继承 BaseFetcher：openFDA 是公开 API 无反爬，但 session 复用和
    拟人化 header 仍能降低被风控的概率（240 req/min/IP）。
    """

    BASE_URL = "https://api.fda.gov/drug/drugsfda.json"
    DEFAULT_PAGE_SIZE = 1000
    MAX_PAGES = 30  # 30 × 1000 = 30K，覆盖全部历史

    def get_nda_approvals(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        app_types: tuple = ("NDA", "BLA"),
        max_results: int = 1000,
    ) -> List[Dict]:
        """
        获取指定日期范围内的首次获批药物（NDA/BLA ORIG+AP）

        Args:
            date_from: YYYYMMDD
            date_to: YYYYMMDD
            app_types: application_number 前缀，默认 ("NDA", "BLA")，排除 ANDA 仿制药
            max_results: 最多返回多少条
        """
        if not date_from:
            date_from = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
        if not date_to:
            date_to = datetime.now().strftime("%Y%m%d")

        # 查询条件：日期范围内有任一 submission + application_number 前缀
        search_parts = [f"submissions.submission_status_date:[{date_from} TO {date_to}]"]
        if app_types:
            prefix_query = " OR ".join(
                f"openfda.application_number:{t}*" for t in app_types
            )
            search_parts.append(f"({prefix_query})")

        params = {
            "search": " AND ".join(search_parts),
            "limit": self.DEFAULT_PAGE_SIZE,
            "sort": "submissions.submission_status_date:desc",
        }

        # 拉多页
        all_apps = self._paginate(params, max_results)

        # 客户端过滤：每个 app 找 ORIG+AP 且日期在范围内的 submission
        results = []
        for app in all_apps:
            orig_sub = self._find_orig_approval(app, date_from, date_to)
            if orig_sub:
                results.append(self._parse_app(app, orig_sub))
                if len(results) >= max_results:
                    break

        return results

    def get_approval_data(
        self, date_from: Optional[str] = None, date_to: Optional[str] = None
    ) -> List[Dict]:
        """兼容 main.py/scheduler 旧签名：接受 YYYY-MM-DD 或 YYYYMMDD"""
        if date_from and "-" in date_from:
            date_from = date_from.replace("-", "")
        if date_to and "-" in date_to:
            date_to = date_to.replace("-", "")
        return self.get_nda_approvals(date_from=date_from, date_to=date_to)

    def _paginate(self, params: Dict, max_results: int) -> List[Dict]:
        """openFDA drugsfda 用 skip 分页（不是 pageToken）"""
        all_results: List[Dict] = []
        skip = 0
        for _ in range(self.MAX_PAGES):
            params["skip"] = skip
            try:
                # BaseFetcher._request 已统一处理 429/5xx 重试 + 反爬检测
                resp = self.get(self.BASE_URL, params=params)
                if resp.status_code == 404:
                    break  # 越界
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                print(f"FDA drugsfda API error: {e}")
                break

            results = data.get("results", [])
            all_results.extend(results)

            if len(results) < self.DEFAULT_PAGE_SIZE:
                break  # 末页
            if len(all_results) >= max_results:
                break
            skip += self.DEFAULT_PAGE_SIZE

        return all_results[:max_results]

    @staticmethod
    def _find_orig_approval(
        app: Dict, date_from: str, date_to: str
    ) -> Optional[Dict]:
        """
        找 type=ORIG AND status=AP 且 submission_status_date 在范围内的 submission
        多个匹配时取日期最大的（最新一次 ORIG 批准）
        """
        candidates = [
            sub
            for sub in app.get("submissions", [])
            if (sub.get("submission_type") == "ORIG"
                and sub.get("submission_status") == "AP"
                and date_from <= sub.get("submission_status_date", "") <= date_to)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.get("submission_status_date", ""))

    @staticmethod
    def _parse_app(app: Dict, orig_sub: Dict) -> Dict:
        products = app.get("products") or [{}]
        product = products[0]
        active_ings = product.get("active_ingredients") or [{}]
        active = active_ings[0].get("name", "") if active_ings else ""

        brand = product.get("brand_name", "")
        sponsor = app.get("sponsor_name", "")
        app_num = app.get("application_number", "")
        approval_date = orig_sub.get("submission_status_date", "")
        review_priority = orig_sub.get("review_priority", "")

        # 构造 title 含连续"批准上市"以触发 APPROVAL 关键词
        # 注意：不要把 application_number 放进 title（否则 "NDA"/"BLA" 会被 NDA_FILING 误匹配）
        title = f"FDA批准上市 {brand} ({active}) - {sponsor} - {approval_date}"

        return {
            "nct_id": "",
            "title": title,
            "source": "FDA",
            "drug_name": brand,
            "company": sponsor,
            "sponsor": sponsor,
            "approval_date": approval_date,
            "date": approval_date,  # 兼容 analyzer date 字段
            "application_number": app_num,
            "review_priority": review_priority,
            "active_ingredient": active,
            "study_url": (
                f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm"
                f"?event=overview.process&ApplNo={app_num}"
            ),
        }


# ---- Smoke test ----

def _smoke_basic() -> None:
    """1. 真实拉取近 90 天 NDA/BLA ORIG+AP"""
    f = FDAFetcher()
    today = datetime.now().strftime("%Y%m%d")
    ninety_days_ago = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    results = f.get_nda_approvals(date_from=ninety_days_ago, date_to=today)
    print(f"[1] 近 90 天 NDA/BLA ORIG 获批: {len(results)} 条")
    assert len(results) > 0, "应至少返回 1 条"
    if results:
        sample = results[0]
        assert sample["source"] == "FDA"
        assert sample["drug_name"]
        assert sample["sponsor"]
        assert sample["approval_date"]
        assert "批准" in sample["title"]
        print(f"    样本: {sample['application_number']} | {sample['drug_name']} | "
              f"sponsor={sample['sponsor'][:30]} | date={sample['approval_date']}")


def _smoke_anda_excluded() -> None:
    """2. 仿制药 ANDA 不在结果中"""
    f = FDAFetcher()
    today = datetime.now().strftime("%Y%m%d")
    ninety_days_ago = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    results = f.get_nda_approvals(date_from=ninety_days_ago, date_to=today)
    for r in results:
        assert r["application_number"].startswith(("NDA", "BLA")), \
            f"混入非 NDA/BLA: {r['application_number']}"
    print(f"[2] 全部 {len(results)} 条都是 NDA/BLA 前缀（ANDA 已排除）")


def _smoke_legacy_compat() -> None:
    """3. 旧签名 get_approval_data('YYYY-MM-DD', 'YYYY-MM-DD') 仍能用"""
    f = FDAFetcher()
    today = datetime.now().strftime("%Y-%m-%d")
    ninety_days_ago = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    results = f.get_approval_data(date_from=ninety_days_ago, date_to=today)
    print(f"[3] 旧签名 get_approval_data(YYYY-MM-DD) → {len(results)} 条")
    assert isinstance(results, list)


def _smoke_analyzer_integration() -> None:
    """4. 与 catalyst analyzer 集成：能识别出 APPROVAL 事件"""
    from analyzer.catalyst import CatalystAnalyzer
    f = FDAFetcher()
    today = datetime.now().strftime("%Y%m%d")
    ninety_days_ago = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    approvals = f.get_nda_approvals(date_from=ninety_days_ago, date_to=today)

    analyzer = CatalystAnalyzer()
    events = analyzer.analyze_events(approvals)
    type_counts: Dict[str, int] = {}
    for e in events:
        type_counts[e.event_type] = type_counts.get(e.event_type, 0) + 1
    print(f"[4] analyzer 识别 {len(events)} 个事件 / {len(approvals)} 条数据 → {type_counts}")
    assert type_counts.get("APPROVAL", 0) > 0, "应至少识别 1 个 APPROVAL"


if __name__ == "__main__":
    print("=" * 60)
    print("FDA Fetcher Smoke Test")
    print("=" * 60)
    _smoke_basic()
    _smoke_anda_excluded()
    _smoke_legacy_compat()
    _smoke_analyzer_integration()
    print("=" * 60)
    print("All smoke tests passed.")
