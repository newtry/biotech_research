# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# 创新药行业动态追踪系统

用于追踪创新药行业动态，辅助二级市场投资决策。

## 数据源

| 数据类型 | 来源 | API/地址 |
|---------|------|---------|
| FDA 审批 | FDA Drugs@FDA | https://drugs@fda.gov |
| NMPA 审批 | CDE 药品审评中心 | https://www.cde.org.cn |
| 临床试验 | ClinicalTrials.gov | https://clinicaltrials.gov API |
| 靶点热度 | 医药魔方 | https://pharmcube.com |
| 上市公司公告 | 交易所披露 | 上交所/深交所/港交所官网 |
| 投融资 | 医药魔方、药融云 | 公开页面 |

## 技术栈

- Python 3.x
- requests / httpx — HTTP 请求
- BeautifulSoup / lxml — 解析 HTML
- pandas — 数据处理
- APScheduler — 定时任务
- itchat — 微信推送（个人微信）
- Server 酱 / PushPlus — 微信消息推送

## 项目结构

```
./
├── fetcher/              # 数据获取模块
│   ├── fda.py           # FDA 审批数据
│   ├── cde.py           # CDE/NMPA 审批数据
│   ├── clinical_trials.py # 临床试验数据
│   ├── pharmcube.py     # 靶点/投融资数据
│   └── announcements.py  # 上市公司公告
├── analyzer/             # 数据分析模块
│   └── catalyst.py      # 催化剂事件识别
├── pusher/              # 推送模块
│   └── wechat.py        # 微信推送
├── scheduler/           # 定时任务
│   └── jobs.py          # 定时任务配置
├── config.py            # 配置文件
└── main.py              # 入口文件
```

## 核心催化剂事件

以下事件通常影响股价，需重点追踪：
- 临床三期揭盲结果
- NDA/BLA 申报
- FDA/NMPA 审批结果（PDUFA 日期）
- 临床失败/终止
- 管线进展（首例患者入组等）

## 微信推送配置

使用 Server 酱（sc.ftqq.com）免费版：
1. 登录 sc.ftqq.com 获取 SCKEY
2. 填入 config.py

或使用 itchat（需扫码登录个人微信）