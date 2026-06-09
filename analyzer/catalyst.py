"""
Catalyst Event Analyzer
识别并分类催化剂事件，生成投资提示

支持两类数据：
- 文本型（公告、新闻、审批）：title + content 关键词匹配
- 结构化型（临床试验）：interventions / sponsor / phases / conditions / nct_id
"""
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CatalystEvent:
    """催化剂事件

    字段顺序：所有无默认值的字段放最前，避免 Python 3.9 dataclass 报错
    """
    # 必填字段
    event_type: str          # APPROVAL, PHASE3_READOUT, NDA_FILING, ...
    drug_name: str
    company: str
    date: str
    description: str
    # 可选字段（带默认值）
    stock_code: str = ""
    nct_id: str = ""
    phase: str = ""
    target: str = ""
    sponsor: str = ""
    study_url: str = ""
    importance: str = "MEDIUM"
    source: str = ""


# 催化剂事件关键词
# 拆分 PHASE3_READOUT / PHASE3_START：
# - PHASE3_READOUT 用强关键词（揭盲/top-line）避免临床试验标题里的"Phase 3"误判
# - PHASE3_START 才接受"III期/Phase 3"
CATALYST_KEYWORDS = {
    'APPROVAL': ['获批', '批准上市', 'NDA批准', 'BLA批准', 'approved', 'approval'],
    'NDA_FILING': ['NDA', 'BLA', '申报上市', '递交上市'],
    'PHASE3_READOUT': ['揭盲', 'top-line', '主要终点', 'primary endpoint', 'readout'],
    'PHASE3_START': ['III期', 'Phase 3', '三期临床', '启动III期', '开始III期', '入组',
                     'enrollment', 'first patient'],
    'CLINICAL_HOLD': ['临床暂停', 'clinical hold'],
    'CLINICAL_FAILURE': ['临床失败', '终止试验', 'failed', 'termination'],
    'FAST_TRACK': ['突破性治疗', 'fast track', '优先审评'],
    'PARTNERSHIP': ['合作', 'license', 'partner'],
    'CAPITAL_RAISE': ['融资', '定增', 'IPO', 'funding'],
}

# 重要性评分
IMPORTANCE_WEIGHTS = {
    'APPROVAL': 10,
    'CLINICAL_FAILURE': 9,
    'PHASE3_READOUT': 8,
    'NDA_FILING': 7,
    'PHASE3_START': 5,
    'FAST_TRACK': 5,
    'CLINICAL_HOLD': 6,
    'PARTNERSHIP': 4,
    'CAPITAL_RAISE': 3,
}


class CatalystAnalyzer:
    """催化剂事件分析"""

    def analyze_event(self, item: Dict) -> Optional[CatalystEvent]:
        """
        分析单条数据，返回 CatalystEvent 或 None（非催化剂事件）

        Args:
            item: 数据字典，文本型（公告/审批）或结构化型（临床试验）
        """
        title = item.get('title', '')
        content = item.get('content', '')
        source = item.get('source', '')

        # 合并所有文本用于关键词匹配
        text = (title + " " + content).lower()

        # 结构化字段（来自 clinical_trials.py 等）
        interventions = item.get('interventions') or []
        sponsor = item.get('sponsor', '')
        phases = item.get('phases') or []
        conditions = item.get('conditions') or []
        nct_id = item.get('nct_id', '')
        study_url = item.get('study_url', '')

        # 事件类型识别
        event_type = self._classify_event_type(text, source, phases, nct_id)
        if not event_type:
            return None

        return CatalystEvent(
            event_type=event_type,
            drug_name=self._extract_drug_name(item, title),
            company=self._extract_company(item, title),
            date=self._extract_date(item),
            description=title or content[:200],
            importance=self._get_importance(event_type),
            source=source,
            stock_code=item.get('stock_code', ''),
            nct_id=nct_id,
            phase=', '.join(phases),
            target=self._match_target(interventions, conditions, text),
            sponsor=sponsor,
            study_url=study_url,
        )

    def analyze_events(self, items: List[Dict]) -> List[CatalystEvent]:
        """批量分析并按重要性排序"""
        events = []
        for item in items:
            event = self.analyze_event(item)
            if event:
                events.append(event)
        events.sort(key=lambda x: IMPORTANCE_WEIGHTS.get(x.event_type, 0), reverse=True)
        return events

    def _classify_event_type(
        self, text: str, source: str, phases: List[str], nct_id: str
    ) -> Optional[str]:
        """
        事件类型分类：
        - 文本中找关键词
        - 但 PHASE3_READOUT 对临床试验数据要求强关键词（揭盲/top-line），
          避免 CT.gov 试验的"Phase 3 Study of..."被误判为读出
        - 临床试验且 phases 含 PHASE3 → 退化归为 PHASE3_START
        """
        is_clinical_trial = bool(nct_id) or 'ClinicalTrials.gov' in source

        for event_type, keywords in CATALYST_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in text:
                    # 临床试验数据：只接受强关键词触发 PHASE3_READOUT
                    if event_type == 'PHASE3_READOUT' and is_clinical_trial:
                        if not self._has_readout_signal(text):
                            continue
                    return event_type

        # 临床试验且 phases 含 PHASE3 → 退化为 PHASE3_START 候选
        if is_clinical_trial and any('PHASE3' in p.upper() for p in phases):
            return 'PHASE3_START'

        return None

    def _has_readout_signal(self, text: str) -> bool:
        """PHASE3_READOUT 强信号：揭盲 / top-line / 主要终点 / readout"""
        return any(kw in text for kw in
                   ['揭盲', 'top-line', '主要终点', 'primary endpoint', 'readout'])

    def _extract_drug_name(self, item: Dict, fallback: str) -> str:
        """
        药物名提取优先级：
        1. 结构化 interventions 中 type=DRUG/BIOLOGICAL 的 name
        2. 结构化 interventions 中第一个非空 name（排除 Placebo/Drug 通用名）
        3. item['drug_name'] 字段（FDA/公告 fetcher 直接填的）
        4. title 首个 token（兜底）
        """
        skip_generic = {'placebo', 'drug'}
        interventions = item.get('interventions') or []

        # 1. 优先 DRUG/BIOLOGICAL 类型
        for i in interventions:
            t = i.get('type', '').upper()
            n = i.get('name', '').strip()
            if n and t in ('DRUG', 'BIOLOGICAL') and n.lower() not in skip_generic:
                return n
        # 2. 退化到第一个有意义的 name
        for i in interventions:
            n = i.get('name', '').strip()
            if n and n.lower() not in skip_generic:
                return n
        # 3. 直接读 item['drug_name']（FDA / 公告等 fetcher 已填的）
        item_drug = item.get('drug_name', '').strip()
        if item_drug and item_drug.lower() not in skip_generic:
            return item_drug
        # 4. title 首个 token
        if fallback:
            return fallback.split()[0]
        return ""

    def _extract_company(self, item: Dict, fallback: str) -> str:
        """
        公司名提取优先级：
        1. 结构化 sponsor 字段（来自临床试验）
        2. PHARMA_STOCKS 关键词匹配 title
        """
        sponsor = item.get('sponsor', '')
        if sponsor:
            return sponsor
        try:
            from config import Config
            title_text = item.get('title', '') or fallback or ''
            for stock_name in Config.PHARMA_STOCKS:
                if stock_name and stock_name in title_text:
                    return stock_name
        except Exception:
            pass
        return ""

    def _match_target(self, interventions, conditions, text) -> str:
        """从干预项、条件、文本中匹配 Config.TARGETS_OF_INTEREST，返回首个命中"""
        try:
            from config import Config
            haystack = ' '.join([
                ' '.join(i.get('name', '') for i in interventions),
                ' '.join(conditions or []),
                text,
            ]).lower()
            for target in Config.TARGETS_OF_INTEREST:
                if target.lower() in haystack:
                    return target
        except Exception:
            pass
        return ""

    def _extract_date(self, item: Dict) -> str:
        """多源日期回退：date → publish_date → last_update_date → approval_date → today"""
        for key in ('date', 'publish_date', 'last_update_date',
                    'approval_date', 'primary_completion_date'):
            val = item.get(key, '')
            if val:
                return str(val)[:10]
        return datetime.now().strftime("%Y-%m-%d")

    def _get_importance(self, event_type: str) -> str:
        weight = IMPORTANCE_WEIGHTS.get(event_type, 5)
        if weight >= 8:
            return "HIGH"
        elif weight >= 5:
            return "MEDIUM"
        return "LOW"

    def generate_report(self, events: List[CatalystEvent]) -> str:
        if not events:
            return "今日无重大催化剂事件"

        report_lines = [
            f"📊 创新药催化剂日报 - {datetime.now().strftime('%Y-%m-%d')}",
            f"共发现 {len(events)} 个重要事件\n",
            "=" * 50,
        ]

        by_type: Dict[str, List[CatalystEvent]] = {}
        for event in events:
            by_type.setdefault(event.event_type, []).append(event)

        type_name = {
            'APPROVAL': '🟢 获批上市',
            'NDA_FILING': '🔵 申报上市',
            'PHASE3_READOUT': '🔴 三期揭盲',
            'PHASE3_START': '⚪ 三期启动',
            'CLINICAL_HOLD': '⚠️ 临床暂停',
            'CLINICAL_FAILURE': '❌ 临床失败',
            'FAST_TRACK': '⭐ 突破性治疗',
            'PARTNERSHIP': '🤝 合作',
            'CAPITAL_RAISE': '💰 融资',
        }

        for event_type, type_events in by_type.items():
            label = type_name.get(event_type, event_type)
            report_lines.append(f"\n{label}")
            for event in type_events:
                # 主体行：药物 (公司)
                who = event.company or event.sponsor or event.stock_code or "未知"
                report_lines.append(f"  • {event.drug_name or '?'} ({who})")
                # 元信息行
                meta_parts = []
                if event.target:
                    meta_parts.append(f"靶点={event.target}")
                if event.phase:
                    meta_parts.append(f"阶段={event.phase}")
                if event.nct_id:
                    meta_parts.append(f"NCT={event.nct_id}")
                if event.date:
                    meta_parts.append(f"日期={event.date}")
                if meta_parts:
                    report_lines.append(f"    {' | '.join(meta_parts)}")
                if event.study_url:
                    report_lines.append(f"    链接: {event.study_url}")
                report_lines.append(f"    来源: {event.source} | 重要性: {event.importance}")

        return "\n".join(report_lines)


# ---- Smoke test ----

def _smoke_dataclass() -> None:
    """1. 预存的 dataclass bug 已修复：必填字段 + 默认字段混合能正常实例化"""
    ev = CatalystEvent(
        event_type="APPROVAL",
        drug_name="X",
        company="Y",
        date="2026-06-09",
        description="desc",
    )
    assert ev.importance == "MEDIUM"
    assert ev.stock_code == ""
    print(f"[1] dataclass 实例化 OK → {ev.event_type}/{ev.drug_name} (importance={ev.importance})")


def _smoke_text_classification() -> None:
    """2. 纯文本（公告/审批）关键词分类保持兼容"""
    a = CatalystAnalyzer()
    items = [
        {'title': '百济神州 PD-1 III期临床试验揭盲', 'source': 'Eastmoney'},  # 公告里有 III期 + 揭盲
        {'title': '君实生物 JS001 获批上市', 'source': 'NMPA'},
        {'title': '信达生物与礼来合作终止', 'source': 'Company'},  # 无催化剂
    ]
    events = a.analyze_events(items)
    types = [e.event_type for e in events]
    print(f"[2] 文本分类 → {types}")
    # 恒瑞(揭盲)→PHASE3_READOUT, 君实(获批)→APPROVAL, 信达(合作)→PARTNERSHIP
    assert 'PHASE3_READOUT' in types
    assert 'APPROVAL' in types
    assert 'PARTNERSHIP' in types
    assert len(events) == 3


def _smoke_structured_fields() -> None:
    """3. 结构化字段：drug_name 从 interventions 提，company 从 sponsor 提"""
    a = CatalystAnalyzer()
    item = {
        'title': 'A Phase 3 Study of Pembrolizumab',
        'source': 'ClinicalTrials.gov',
        'nct_id': 'NCT01234567',
        'sponsor': 'Merck Sharp & Dohme LLC',
        'phases': ['PHASE3'],
        'interventions': [
            {'type': 'DRUG', 'name': 'Pembrolizumab'},
            {'type': 'DRUG', 'name': 'Placebo'},
        ],
        'conditions': ['Melanoma'],
        'last_update_date': '2026-05-15',
    }
    ev = a.analyze_event(item)
    assert ev is not None, "临床试验含 PHASE3 应被识别"
    assert ev.drug_name == 'Pembrolizumab', f"应从 interventions 取 drug: {ev.drug_name}"
    assert ev.company == 'Merck Sharp & Dohme LLC', f"应从 sponsor 取公司: {ev.company}"
    assert ev.nct_id == 'NCT01234567'
    assert ev.sponsor == 'Merck Sharp & Dohme LLC'
    assert ev.date == '2026-05-15', f"应从 last_update_date 取: {ev.date}"
    assert ev.phase == 'PHASE3'
    assert ev.event_type == 'PHASE3_START', \
        f"无 readout 关键词的临床试验应归 PHASE3_START, got {ev.event_type}"
    print(f"[3] 结构化字段 OK → drug={ev.drug_name} company={ev.company[:20]} "
          f"type={ev.event_type} date={ev.date}")


def _smoke_target_matching() -> None:
    """4. target 字段从 intervention 名称/condition 匹配 Config.TARGETS_OF_INTEREST"""
    a = CatalystAnalyzer()
    item = {
        'title': 'Study of PD-L1 antibody in lung cancer',
        'source': 'ClinicalTrials.gov',
        'nct_id': 'NCT99999999',
        'phases': ['PHASE3'],
        'interventions': [{'type': 'DRUG', 'name': 'Anti-PD-L1 mAb'}],
        'conditions': ['Non-Small Cell Lung Carcinoma'],
    }
    ev = a.analyze_event(item)
    assert ev is not None
    assert ev.target == 'PD-L1', f"应匹配 PD-L1: {ev.target}"
    print(f"[4] target 匹配 OK → {ev.target}")


def _smoke_date_fallback() -> None:
    """5. date 多源回退：last_update_date > approval_date > today"""
    a = CatalystAnalyzer()
    # 5a: 多个 date key 同时存在，优先 date
    ev1 = a.analyze_event({
        'title': 'X 获批上市',
        'date': '2026-01-15',
        'publish_date': '2026-02-20',
        'last_update_date': '2026-03-10',
    })
    assert ev1.date == '2026-01-15', f"应优先 date 字段: {ev1.date}"

    # 5b: 无 date，回退到 publish_date
    ev2 = a.analyze_event({
        'title': 'X 获批上市',
        'publish_date': '2026-02-20',
    })
    assert ev2.date == '2026-02-20', f"应回退到 publish_date: {ev2.date}"

    # 5c: 都没有，回退到今天
    ev3 = a.analyze_event({'title': 'X 获批上市'})
    assert ev3.date == datetime.now().strftime('%Y-%m-%d'), f"应回退到今天: {ev3.date}"
    print(f"[5] date 回退 OK → 优先={ev1.date}, publish={ev2.date}, today={ev3.date}")


def _smoke_clinical_trial_anti_misclassify() -> None:
    """6. 临床试验的 'Phase 3 Study of X' 不应被误判为 PHASE3_READOUT"""
    a = CatalystAnalyzer()
    item = {
        'title': 'A Randomized Phase 3 Study of Drug X in Oncology',
        'source': 'ClinicalTrials.gov',
        'nct_id': 'NCT00000001',
        'phases': ['PHASE3'],
        'interventions': [{'type': 'DRUG', 'name': 'Drug X'}],
    }
    ev = a.analyze_event(item)
    assert ev is not None
    assert ev.event_type == 'PHASE3_START', \
        f"无 readout 关键词应归 PHASE3_START, got {ev.event_type}"
    print(f"[6] 临床试验防误判 OK → {ev.event_type}（不是 PHASE3_READOUT）")


def _smoke_non_catalyst() -> None:
    """7. 非催化剂文本返回 None"""
    a = CatalystAnalyzer()
    ev = a.analyze_event({'title': '今天天气不错', 'source': 'Weather'})
    assert ev is None, f"无关文本应返回 None: {ev}"
    print("[7] 非催化剂文本 → None")


def _smoke_report_with_structured() -> None:
    """8. 报告生成含 nct_id/target/phase 元信息"""
    a = CatalystAnalyzer()
    items = [
        {
            'title': 'Phase 3 Study of PD-1',
            'source': 'ClinicalTrials.gov',
            'nct_id': 'NCT01234567',
            'sponsor': 'Test Pharma',
            'phases': ['PHASE3'],
            'interventions': [{'type': 'DRUG', 'name': 'PD-1 Inhibitor'}],
            'conditions': ['Cancer'],
            'study_url': 'https://clinicaltrials.gov/study/NCT01234567',
            'last_update_date': '2026-05-01',
        }
    ]
    events = a.analyze_events(items)
    report = a.generate_report(events)
    assert 'NCT=NCT01234567' in report, "报告应含 NCT ID"
    assert '靶点=PD-1' in report, "报告应含靶点"
    assert '阶段=PHASE3' in report, "报告应含阶段"
    assert 'clinicaltrials.gov/study/NCT01234567' in report, "报告应含链接"
    print(f"[8] 报告生成 OK（含结构化元信息）")
    print("--- 报告片段 ---")
    print("\n".join(report.split("\n")[:10]))


if __name__ == "__main__":
    print("=" * 60)
    print("Catalyst Analyzer Smoke Test")
    print("=" * 60)
    _smoke_dataclass()
    _smoke_text_classification()
    _smoke_structured_fields()
    _smoke_target_matching()
    _smoke_date_fallback()
    _smoke_clinical_trial_anti_misclassify()
    _smoke_non_catalyst()
    _smoke_report_with_structured()
    print("=" * 60)
    print("All smoke tests passed.")
