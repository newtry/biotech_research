"""
Scheduler Configuration
定时任务配置
"""
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def daily_digest_job():
    """
    每日行业动态摘要任务
    建议交易日上午 8:30 运行
    """
    from fetcher.fda import FDAFetcher
    from fetcher.cde import CDEFetcher
    from fetcher.announcements import AnnouncementFetcher
    from fetcher.clinical_trials import ClinicalTrialsFetcher
    from analyzer.catalyst import CatalystAnalyzer
    from pusher.wechat import WechatPusher
    from config import Config

    config = Config()
    analyzer = CatalystAnalyzer()
    pusher = WechatPusher(channel=config.WECHAT_CHANNEL, sc_key=config.SCKEY)

    logger.info("开始获取行业数据...")

    # 1. 获取 FDA 审批（最近一周）
    fda = FDAFetcher()
    fda_data = fda.get_approval_data(
        date_from=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    )

    # 2. 获取 CDE 审批
    cde = CDEFetcher()
    cde_data = cde.get_drug_approval()

    # 3. 获取临床试验进展（关键三期）
    ct = ClinicalTrialsFetcher()
    ct_data = ct.get_phase3_trials(limit=30)

    # 4. 获取公告（医药行业）
    ann = AnnouncementFetcher()
    keywords = ["临床试验", "III期", "NDA", "BLA", "获批", "临床结果"]
    ann_data = ann.search_pharma_announcements(
        keywords=keywords,
        date_from=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    )

    # 合并所有数据
    all_data = []
    all_data.extend([{'title': d.get('drug_name', ''), 'source': d.get('source', '')} for d in fda_data])
    all_data.extend([{'title': d.get('drug_name', ''), 'source': d.get('source', '')} for d in cde_data])
    all_data.extend([{'title': d.get('title', ''), 'source': d.get('source', '')} for d in ct_data])
    all_data.extend(ann_data)

    # 5. 分析催化剂事件
    events = analyzer.analyze_events(all_data)

    # 6. 生成报告并推送
    report = analyzer.generate_report(events)
    logger.info(f"报告内容:\n{report}")

    # 7. 微信推送
    if config.SCKEY:
        pusher.push("创新药催化剂日报", report)
        logger.info("微信推送成功")
    else:
        logger.info("未配置 SCKEY，跳过推送")

    return report


def run_scheduler():
    """运行定时调度器"""
    scheduler = BlockingScheduler()

    # 每日早上 8:30 运行
    scheduler.add_job(
        daily_digest_job,
        CronTrigger(hour=8, minute=30, timezone="Asia/Shanghai"),
        id="daily_digest",
        name="每日行业动态摘要"
    )

    # 交易日 15:00 后运行收盘总结
    scheduler.add_job(
        daily_digest_job,
        CronTrigger(hour=15, minute=0, timezone="Asia/Shanghai"),
        id="daily_summary",
        name="每日收盘总结"
    )

    logger.info("调度器启动")
    scheduler.start()


if __name__ == "__main__":
    # 测试运行
    daily_digest_job()