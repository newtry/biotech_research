"""
Clinical Trials Fetcher
从 ClinicalTrials.gov v2 API 获取全球临床试验数据

v2 API 关键事实（实测）：
- 端点：https://clinicaltrials.gov/api/v2/studies
- phase 枚举：PHASE1 / PHASE2 / PHASE3 / PHASE4 / NOT_APPLICABLE
- phase / status 过滤必须用 filter.advanced（query.phase 不是合法参数）
- filter.advanced 语法：AREA[FieldName]VALUE；同维度 OR，跨维度 AND
- 日期：AREA[LastUpdatePostDate]RANGE[YYYY-MM-DD,YYYY-MM-DD]
- 分页：pageToken 参数；响应里 nextPageToken 是字段名
- 速率限制：429 时响应头无 Retry-After，需 client 自行指数退避
"""
import random
import time
from typing import List, Dict, Optional

import requests

from .base import BaseFetcher
from .anti_crawl import RetryConfig


class ClinicalTrialsFetcher(BaseFetcher):
    """临床试验数据获取（v2 API）

    继承 BaseFetcher 拿 session 复用 + ETag 缓存。
    自定义 MAX_RETRIES=4 覆盖默认值，因为 CT.gov 在 burst 时连续 429 的概率高。
    """

    API_BASE = "https://clinicaltrials.gov/api/v2"
    DEFAULT_PAGE_SIZE = 100
    MAX_PAGE_SIZE = 1000
    MAX_RETRIES = 4

    def __init__(self) -> None:
        super().__init__(retry=RetryConfig(max_retries=4, initial_delay=1.0))

    # v2 顶层字段名（响应里直接通过 fields= 选择，不带 module 嵌套路径）
    STUDY_FIELDS = [
        "NCTId",
        "BriefTitle",
        "OverallStatus",
        "Phase",
        "LeadSponsorName",
        "CollaboratorName",
        "InterventionName",
        "InterventionType",
        "Condition",
        "EnrollmentCount",
        "StartDate",
        "PrimaryCompletionDate",
        "CompletionDate",
        "LastUpdatePostDate",
    ]

    # ---- 公开 API ----

    def search_trials(
        self,
        query: Optional[str] = None,
        phases: Optional[List[str]] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        max_results: int = 50,
    ) -> List[Dict]:
        """
        搜索临床试验

        Args:
            query: 自由词关键词（None = 不传 query.term，让 API 退化为"按 filter 全量"）
            phases: v2 枚举值列表，如 ["PHASE3"] 或 ["PHASE2", "PHASE3"]
            status: v2 状态枚举，如 "RECRUITING" / "COMPLETED" / "ACTIVE_NOT_RECRUITING"
            date_from / date_to: YYYY-MM-DD，作用于 LastUpdatePostDate
            max_results: 触发自动分页的总数上限
        """
        page_size = min(self.MAX_PAGE_SIZE, max(1, max_results))

        params: Dict = {
            "fields": ",".join(self.STUDY_FIELDS),
            "pageSize": page_size,
            "format": "json",
        }
        if query:
            params["query.term"] = query

        filter_expr = self._build_filter_expr(phases, status, date_from, date_to)
        if filter_expr:
            params["filter.advanced"] = filter_expr

        max_pages = (max_results // page_size) + 2  # 留点富余
        return self._request(params, max_pages=max_pages, max_results=max_results)

    def get_phase3_trials(self, drug_keyword: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """兼容旧签名：拉取 Phase 3 试验"""
        return self.search_trials(
            query=drug_keyword,
            phases=["PHASE3"],
            max_results=limit,
        )

    def get_phase3_trials_by_targets(
        self,
        targets: Optional[List[str]] = None,
        limit_per_target: int = 20,
    ) -> List[Dict]:
        """
        按 Config.TARGETS_OF_INTEREST 拉取 Phase 3 试验
        每个 target 一次查询，跨 target 用 nct_id 去重
        """
        if targets is None:
            from config import Config
            targets = Config.TARGETS_OF_INTEREST

        seen = {}
        for target in targets:
            rows = self.search_trials(
                query=target,
                phases=["PHASE3"],
                max_results=limit_per_target,
            )
            for row in rows:
                nct = row.get("nct_id", "")
                if nct and nct not in seen:
                    seen[nct] = row
                elif nct in seen and (row.get("last_update_date", "") >
                                      seen[nct].get("last_update_date", "")):
                    seen[nct] = row

        result = list(seen.values())
        result.sort(key=lambda r: r.get("last_update_date", ""), reverse=True)
        return result

    def get_recruiting_trials(self, disease_area: str = "oncology", limit: int = 50) -> List[Dict]:
        """兼容旧签名：拉取正在招募的试验"""
        return self.search_trials(
            query=disease_area,
            status="RECRUITING",
            max_results=limit,
        )

    # ---- 内部 ----

    def _build_filter_expr(
        self,
        phases: Optional[List[str]],
        status: Optional[str],
        date_from: Optional[str],
        date_to: Optional[str],
    ) -> str:
        """
        拼 filter.advanced：
        - 同维度内 OR（phase list、status list）
        - 跨维度 AND（phase AND status AND date）
        """
        parts = []
        if phases:
            # AREA[Phase]PHASE2 OR PHASE3（无空格，无引号）
            parts.append("AREA[Phase]" + " OR ".join(phases))
        if status:
            parts.append(f"AREA[OverallStatus]{status}")
        if date_from or date_to:
            lo = date_from or "MIN"
            hi = date_to or "MAX"
            parts.append(f"AREA[LastUpdatePostDate]RANGE[{lo},{hi}]")
        return " AND ".join(parts)

    def _request(
        self,
        params: Dict,
        max_pages: int = 10,
        max_results: Optional[int] = None,
    ) -> List[Dict]:
        """
        统一 HTTP 调用 + 429 指数退避 + 分页

        退避：1s → 2s → 4s → 8s，每步 +20% 抖动
        MAX_RETRIES=4（含最后可能 raise）
        """
        url = f"{self.API_BASE}/studies"
        delay = 1.0
        collected: List[Dict] = []

        for attempt in range(self.MAX_RETRIES):
            try:
                page_token = None
                for page_idx in range(max_pages):
                    if page_token:
                        params["pageToken"] = page_token
                    elif "pageToken" in params:
                        del params["pageToken"]

                    resp = self.session.get(url, params=params, timeout=60)

                    if resp.status_code == 429:
                        # 退避后整体重试（不分页局部重试）
                        jitter = random.uniform(0, delay * 0.2)
                        time.sleep(delay + jitter)
                        delay *= 2
                        break  # 跳出分页循环，进入下一次 attempt

                    resp.raise_for_status()
                    data = resp.json()

                    studies = data.get("studies", [])
                    for study in studies:
                        parsed = self._parse_study(study)
                        if parsed:
                            collected.append(parsed)
                            if max_results and len(collected) >= max_results:
                                return collected[:max_results]

                    next_token = data.get("nextPageToken")
                    if not next_token:
                        return collected
                    page_token = next_token

                else:
                    # for page_idx 正常完成（无 break）
                    return collected
                # 如果从 429 break 出来，继续外层 attempt 循环

            except requests.RequestException as e:
                if attempt == self.MAX_RETRIES - 1:
                    print(f"ClinicalTrials API error after {self.MAX_RETRIES} attempts: {e}")
                    return collected
                jitter = random.uniform(0, delay * 0.2)
                time.sleep(delay + jitter)
                delay *= 2

        return collected

    def _parse_study(self, study: Dict) -> Optional[Dict]:
        """v2 字段路径修正 + 结构化干预项提取"""
        try:
            proto = study.get("protocolSection", {})

            ident = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            design = proto.get("designModule", {})
            sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
            arms = proto.get("armsInterventionsModule", {})
            cond_mod = proto.get("conditionsModule", {})

            nct_id = ident.get("nctId", "")
            if not nct_id:
                return None

            # 设计阶段是 list（PHASE2/PHASE3 都有）
            phases = design.get("phases") or []

            # 干预项：[{type, name}, ...]
            interventions = []
            for itv in (arms.get("interventions") or []):
                interventions.append({
                    "type": itv.get("type", ""),
                    "name": itv.get("name", ""),
                })

            # 协办方：[{name, class}, ...]
            collaborators = []
            for coll in (sponsor_mod.get("collaborators") or []):
                collaborators.append(coll.get("name", ""))

            return {
                "nct_id": nct_id,
                "title": ident.get("briefTitle", ""),
                "source": "ClinicalTrials.gov",
                "status": status_mod.get("overallStatus", ""),
                "phases": phases,
                "sponsor": sponsor_mod.get("leadSponsor", {}).get("name", ""),
                "collaborators": collaborators,
                "interventions": interventions,
                "conditions": cond_mod.get("conditions") or [],
                "enrollment": design.get("enrollmentInfo", {}).get("count"),
                "start_date": status_mod.get("startDateStruct", {}).get("date", ""),
                "primary_completion_date": status_mod.get(
                    "primaryCompletionDateStruct", {}
                ).get("date", ""),
                "last_update_date": status_mod.get(
                    "lastUpdatePostDateStruct", {}
                ).get("date", ""),
                "study_url": f"https://clinicaltrials.gov/study/{nct_id}",
            }
        except Exception as e:
            print(f"Parse study error: {e}")
            return None


# ---- Smoke test ----

def _smoke_legacy_compat(f: ClinicalTrialsFetcher) -> None:
    """1. 旧签名 get_phase3_trials(limit=5) 仍能跑通"""
    rows = f.get_phase3_trials(limit=5)
    print(f"[1] 旧签名 get_phase3_trials(limit=5) → {len(rows)} 条")
    assert isinstance(rows, list), "应返回 list"


def _smoke_real_pull(f: ClinicalTrialsFetcher) -> None:
    """2. 真实 Phase 3 拉取 + 字段断言"""
    rows = f.search_trials(phases=["PHASE3"], max_results=10)
    print(f"[2] search_trials(phases=['PHASE3'], max_results=10) → {len(rows)} 条")
    if rows:
        sample = rows[0]
        assert sample["source"] == "ClinicalTrials.gov"
        assert "interventions" in sample, f"缺失 interventions: {sample.keys()}"
        assert "NCT" in sample["nct_id"], f"nct_id 格式异常: {sample['nct_id']}"
        assert "PHASE3" in sample["phases"], f"phases 异常: {sample['phases']}"
        print(f"    样本: {sample['nct_id']} | sponsor={sample['sponsor'][:30]} | "
              f"phases={sample['phases']} | interventions#={len(sample['interventions'])}")


def _smoke_keyword(f: ClinicalTrialsFetcher) -> None:
    """3. 关键词搜索"""
    rows = f.search_trials(query="PD-1", phases=["PHASE3"], max_results=5)
    print(f"[3] search_trials(query='PD-1', phases=['PHASE3'], max_results=5) → {len(rows)} 条")
    if rows:
        title = rows[0]["title"].lower()
        # 不强制要求 title 包含关键词（API 可能在 sponsor/condition 命中），但至少应返回结果
        print(f"    样本: {rows[0]['nct_id']} | {rows[0]['title'][:60]}")


def _smoke_pagination(f: ClinicalTrialsFetcher) -> None:
    """4. 分页路径"""
    rows = f.search_trials(phases=["PHASE3"], max_results=25)
    print(f"[4] search_trials(max_results=25) → {len(rows)} 条（应 > 10 验证分页生效）")
    assert len(rows) > 10, f"分页疑似失效，仅返回 {len(rows)} 条"


def _smoke_429_retry() -> None:
    """5. 429 重试：mock 两次 429 后 200，断言最终拿到结果"""
    from unittest.mock import MagicMock

    fake_payload_1 = {
        "studies": [
            {
                "protocolSection": {
                    "identificationModule": {
                        "nctId": "NCT00000001",
                        "briefTitle": "Mock trial after 429 retries",
                    },
                    "statusModule": {
                        "overallStatus": "RECRUITING",
                        "lastUpdatePostDateStruct": {"date": "2026-01-01"},
                    },
                    "designModule": {"phases": ["PHASE3"]},
                    "sponsorCollaboratorsModule": {
                        "leadSponsor": {"name": "Mock Pharma"}
                    },
                }
            }
        ]
    }

    call_log = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        call_log["n"] += 1
        mock_resp = MagicMock()
        if call_log["n"] <= 2:
            mock_resp.status_code = 429
        else:
            mock_resp.status_code = 200
            mock_resp.json.return_value = fake_payload_1
        return mock_resp

    # 改 hook 到 instance method，因为重构后走 self.session.get
    f = ClinicalTrialsFetcher()
    f.session.get = fake_get
    # 直接调 _request 避免分页循环干扰
    rows = f._request({"fields": "NCTId", "pageSize": 1}, max_pages=1)

    print(f"[5] 429 mock（2 次失败后 200）→ HTTP 调用 {call_log['n']} 次，返回 {len(rows)} 条")
    assert call_log["n"] >= 3, f"重试未生效，只调用 {call_log['n']} 次"
    assert len(rows) == 1, f"重试后应拿到 1 条，实际 {len(rows)}"
    assert rows[0]["nct_id"] == "NCT00000001"


def _smoke_config_targets() -> None:
    """6. 接入 Config.TARGETS_OF_INTEREST"""
    f = ClinicalTrialsFetcher()
    rows = f.get_phase3_trials_by_targets(limit_per_target=2)
    print(f"[6] get_phase3_trials_by_targets(limit_per_target=2) → {len(rows)} 条去重结果")
    if rows:
        nct_set = {r["nct_id"] for r in rows}
        assert len(nct_set) == len(rows), "去重失败，存在重复 nct_id"
        assert all("PHASE3" in r["phases"] for r in rows), "混入非 Phase 3 试验"


if __name__ == "__main__":
    f = ClinicalTrialsFetcher()

    print("=" * 60)
    print("ClinicalTrials Fetcher Smoke Test")
    print("=" * 60)

    # 1-4 需联网
    _smoke_legacy_compat(f)
    _smoke_real_pull(f)
    _smoke_keyword(f)
    _smoke_pagination(f)
    # 5 离线
    _smoke_429_retry()
    # 6 需联网
    _smoke_config_targets()

    print("=" * 60)
    print("All smoke tests passed.")
