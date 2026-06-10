# 创新药行业动态追踪系统 — 技术方案

> 状态：v0.1 · 2026-06-09
> 对应代码：本仓库根目录

---

## 一、目标与定位

- **用途**：追踪创新药行业关键事件，辅助二级市场投资决策
- **覆盖事件类型**：FDA/NMPA 审批、NDA/BLA 申报、三期揭盲/启动、临床失败、BD 合作、融资
- **交付形式**：每日通过微信推送结构化日报
- **当前形态**：CLI + 定时任务，非在线服务（无 Web UI、无 DB）

---

## 二、整体架构

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Scheduler  │ ──▶ │  Fetcher ×  │ ──▶ │  Catalyst   │ ──▶ Wechat
│ (APScheduler)│    │  4-5 sources │    │  Analyzer   │     Pusher
└─────────────┘     └─────────────┘     └─────────────┘
      │                    │                    │
   cron trigger         HTTP API          关键词分类
   8:30 / 15:00        + 反爬策略         + 重要性评分
```

四层职责清晰分离，**没有持久化层**（详见后文 TODO）。

---

## 三、模块设计

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 入口 | `main.py` | ✅ | CLI: `fetch / analyze / push / schedule` |
| 配置 | `config.py` | ✅ | 股票池 / 关键词 / 靶点 / 推送时间 / 通道 |
| FDA | `fetcher/fda.py` | ✅ 完整 | openFDA drugsfda，含 30K 历史分页 + ORIG/AP 二次过滤 |
| CDE | `fetcher/cde.py` | ⚠️ 半成品 | 调 CDE 优先审评 API，**未跑通验证** |
| 公告 | `fetcher/announcements.py` | ✅ 核心通 | 走东方财富医药行业公告接口，关键词过滤 |
| 临床试验 | `fetcher/clinical_trials.py` | ✅ 完整 | CT.gov v2 API，含 429 指数退避 + 按靶点去重 |
| 医药魔方/药融云 | `fetcher/pharmcube.py` | ❌ 占位 | 需登录/付费，未实现 |
| 催化剂分析 | `analyzer/catalyst.py` | ✅ 完整 | 文本 + 结构化双路，重要性排序 |
| 微信推送 | `pusher/wechat.py` | ✅ 完整 | Server 酱 / PushPlus 双通道 |
| 调度 | `scheduler/jobs.py` | ⚠️ 有 bug | 详见下文 |

---

## 四、数据流

```
1. Scheduler 触发 (cron: 8:30, 15:00 Asia/Shanghai)
        │
        ▼
2. 拉数（并行或串行）
   - FDA.get_nda_approvals(date_from=now-7d)   → List[Dict]
   - CDE.get_drug_approval()                    → List[Dict]
   - ClinicalTrials.search_trials(phases=[3])   → List[Dict]
   - Announcement.search_pharma_announcements() → List[Dict]
        │
        ▼
3. 统一字段对齐 → 注入 source 标签
        │
        ▼
4. CatalystAnalyzer.analyze_events()  对每条做：
   - 关键词分类（9 类事件）→ event_type
   - drug_name 提取（interventions 优先 > drug_name 字段 > title 兜底）
   - company 提取（sponsor 优先 > 股票池词表）
   - target 匹配（PD-1, ADC, KRAS...）
   - date 回退（date > publish_date > last_update_date > today）
   - 重要性评分 (HIGH/MEDIUM/LOW)
        │
        ▼
5. generate_report() → 文本报告（按类型分组）
        │
        ▼
6. WechatPusher.push() → Server 酱 / PushPlus → 微信
```

---

## 五、催化剂事件模型

### 事件类型（9 类，按重要性降序）

| event_type | 关键词示例 | 重要性 |
|------------|----------|--------|
| APPROVAL | 获批 / 批准上市 / NDA批准 | HIGH (10) |
| CLINICAL_FAILURE | 临床失败 / 终止试验 | HIGH (9) |
| PHASE3_READOUT | 揭盲 / top-line / 主要终点 | HIGH (8) |
| NDA_FILING | NDA / BLA / 申报上市 | MEDIUM (7) |
| CLINICAL_HOLD | 临床暂停 | MEDIUM (6) |
| PHASE3_START | III期 / 入组 / first patient | MEDIUM (5) |
| FAST_TRACK | 突破性治疗 / 优先审评 | MEDIUM (5) |
| PARTNERSHIP | 合作 / license | LOW (4) |
| CAPITAL_RAISE | 融资 / 定增 / IPO | LOW (3) |

### 关键设计：防误判

`PHASE3_READOUT` 在临床试验数据上需要**强关键词**（揭盲/top-line/readout）才会触发，否则只归为 `PHASE3_START`。这是为了避免 CT.gov 上"A Phase 3 Study of X..."这种普通标题被误判为揭盲。已通过 smoke test 验证。

---

## 六、反爬策略

### 6.1 分层策略

| 层级 | 适用 | 工具组合 |
|------|------|----------|
| **L1 友好爬取** | FDA / CT.gov / 官方 API | `requests` + `Session` + 随机 UA + 随机 delay(1-3s) + ETag/If-Modified-Since 缓存 + 指数退避 |
| **L2 中等强度** | CDE / 东方财富 / 部分交易所 | `requests` + 真实 Cookie + Referer 伪造 + 滑块/验证码识别（可选） |
| **L3 重度（JS 渲染）** | 反爬严格的 JS 渲染页面 | `Playwright` 无头 + stealth 插件 + cookie 持久化 |
| **L4 兜底** | 实在拿不到 | 付费 API（医药魔方 / 药融云）/ 第三方数据源 |

### 6.2 设计原则

1. **优先用官方 API**：FDA openFDA、CT.gov v2 是公开 API，无反爬
2. **最小化请求频率**：抓全量 > 增量更新，优先利用 ETag/Last-Modified 减少回源
3. **拟人化**：随机 delay、随机 UA、Referer 完整、cookie 复用
4. **失败优雅降级**：L2 → L3 → L4，遇到 403/JS 渲染时升级
5. **robots.txt 合规**：启动时拉一次，标记禁爬路径
6. **失败告警**：所有 fetcher 的异常必须上报（详见 P0）

### 6.3 实现规划

```
fetcher/
├── base.py              # NEW: BaseFetcher 抽象类 + HumanizedSession
├── anti_crawl.py        # NEW: 反爬工具集（UA 池、delay、ETag 缓存、BrowserFetcher）
├── fda.py               # REFACTOR: 继承 BaseFetcher
├── cde.py               # REFACTOR: 继承 BaseFetcher，L2 不行时降级 L3
├── clinical_trials.py   # REFACTOR: 继承 BaseFetcher
├── announcements.py     # REFACTOR: 继承 BaseFetcher
└── pharmcube.py         # 暂不动（L4 走付费 API）
```

`BaseFetcher` 抽象类统一暴露：
- `self.session: requests.Session`（带 UA、cookie 持久化）
- `self.get(url, **kwargs)` / `self.post(url, **kwargs)`（自动 retry + delay + 缓存）
- `self.fetch_via_browser(url)`（L3 降级入口）

---

## 七、当前实现状态盘点

### ✅ 已完成并有 smoke test 验证

- `fda.py` — 6 个 smoke test（基础拉取、ANDA 排除、API 集成、旧签名兼容）
- `clinical_trials.py` — 6 个 smoke test（含 429 mock 重试）
- `catalyst.py` — 8 个 smoke test（dataclass、文本/结构化双路、target 匹配、date 回退、防误判、报告）
- `wechat.py` — 5 个 smoke test（缺 key、成功、失败、路由、未知 channel）

### ⚠️ 半成品 / 有隐患

- `cde.py` — 代码存在但未跑过；CDE 接口常换/反爬严，**L2 + L3 双方案兜底**
- `announcements.py` — 上交所/深交所/港交所 3 个方法都只 return []，**实际只走东方财富一条路**
- `pharmcube.py` — 完全占位（需付费 token）
- `scheduler/jobs.py`：
  - 用了 `BlockingScheduler`，会阻塞主进程
  - 8:30 和 15:00 两个 job **都调用了 `daily_digest_job`**，没有真正的"收盘总结"
  - 没有去重：每天 8:30 重跑，最近 7 天的同一条 FDA 批准会**重复推**
- `main.py` 的 `push` action 调 `run_analyzer` 又重新生成 `report`，**重复计算**

### ❌ 缺失

- **持久化层**（SQLite / Postgres）：所有数据不落盘，调度一关就丢
- **去重/幂等**：跨天/跨源同一条事件不合并
- **错误告警**：fetcher 静默失败时没有任何通知
- **测试框架**：所有 smoke test 写死在 `if __name__` 里，无 pytest 集成
- **日志**：只有 scheduler 里 `logging.info`，主流程基本是 `print`
- **itchat**：CLAUDE.md 提到但 `requirements.txt` 实际没有

---

## 八、改进优先级（建议路线图）

### P0 — 立刻能跑起来（基础稳固）

1. **反爬基础**：`fetcher/base.py` + `fetcher/anti_crawl.py`，所有 fetcher 接入
2. **scheduler 修复**：拆出真正的 `daily_digest_job` 和 `close_summary_job`
3. **失败告警**：主流程加 try/except + 推送一条"今日拉取失败"
4. **CDE 真实跑通**：L2（API）不行时降级 L3（Playwright）

### P1 — 一周内（质量提升）

5. 引入 SQLite 做轻量持久化（事件去重 + 历史回看）
6. 接入 pytest，把现有 smoke test 迁过去
7. FDA fetcher 的 90 天窗口可配置；增加 CDE 临床试验默示许可数据源
8. 主流程统一 logging（替换 print）

### P2 — 一个月（能力扩展）

9. 真实接入医药魔方（Token 申请 + 异步任务）
10. 引入 LLM 做"非结构化公告 → 催化剂事件"的兜底分类
11. Web UI（Streamlit）做事件看板
12. itchat 通道（已在 pusher/wechat.py 占位但 requirements.txt 缺失）

---

## 九、风险与注意事项

| 风险 | 影响 | 缓解 |
|------|------|------|
| 反爬 / 接口变更 | CDE / 港交所 / 东方财富常变 | L1→L2→L3 降级链，抓 4xx/5xx 立刻告警 |
| 假阳性 | 关键词匹配可能把"突破性治疗公示"误判为"获批" | APPROVAL 类要求 title 含"批准上市"强信号 |
| 重复推送 | 调度每天重跑，7 天窗口会有大量重复 | 去重主键 = (source, drug_name, event_type, date) |
| Server 酱限额 | 免费版每天 5 条 | 触发后降级到 PushPlus |
| 浏览器开销 | Playwright 启动慢、吃内存 | 仅在 L2 失败时降级 L3 |
| venv 体积 | 之前 git 时忽略掉了 | ✅ 已加 .gitignore |
| Playwright 体积 | 浏览器二进制 ~300MB | 用 `playwright install chromium` 按需安装，不进 git |
