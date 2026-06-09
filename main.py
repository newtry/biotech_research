#!/usr/bin/env python3
"""
创新药行业动态追踪系统
主入口文件
"""
import argparse
import sys
from datetime import datetime, timedelta

from fetcher.fda import FDAFetcher
from fetcher.cde import CDEFetcher
from fetcher.announcements import AnnouncementFetcher
from fetcher.clinical_trials import ClinicalTrialsFetcher
from fetcher.pharmcube import PharmcubeFetcher
from analyzer.catalyst import CatalystAnalyzer
from pusher.wechat import WechatPusher
from config import Config


def run_fetcher(args):
    """运行数据获取"""
    config = Config()
    fetcher_map = {
        'fda': FDAFetcher,
        'cde': CDEFetcher,
        'clinical': ClinicalTrialsFetcher,
        'announcements': AnnouncementFetcher,
        'pharmcube': PharmcubeFetcher,
    }

    if args.source == 'all':
        sources = ['fda', 'cde', 'clinical', 'announcements']
    else:
        sources = [args.source]

    results = {}
    for source in sources:
        if source in fetcher_map:
            fetcher = fetcher_map[source]()
            if source == 'fda':
                results[source] = fetcher.get_approval_data(
                    date_from=(datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
                )
            elif source == 'cde':
                results[source] = fetcher.get_drug_approval()
            elif source == 'clinical':
                results[source] = fetcher.get_phase3_trials(limit=50)
            elif source == 'announcements':
                keywords = config.ANNOUNCEMENT_KEYWORDS
                results[source] = fetcher.search_pharma_announcements(
                    keywords=keywords,
                    date_from=(datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
                )

    return results


def run_analyzer(args):
    """运行分析"""
    results = run_fetcher(args)
    analyzer = CatalystAnalyzer()

    all_data = []
    for source, items in results.items():
        for item in items:
            item['source'] = source
            all_data.append(item)

    events = analyzer.analyze_events(all_data)
    report = analyzer.generate_report(events)
    print(report)
    return events


def main():
    parser = argparse.ArgumentParser(description="创新药行业动态追踪系统")
    parser.add_argument('action', choices=['fetch', 'analyze', 'push', 'schedule'],
                        help='操作类型: fetch(获取数据), analyze(分析), push(推送), schedule(定时运行)')
    parser.add_argument('--source', '-s', default='all',
                        help='数据源: fda, cde, clinical, announcements, pharmcube, all')
    parser.add_argument('--days', '-d', type=int, default=7,
                        help='获取最近N天的数据')
    parser.add_argument('--push', '-p', action='store_true',
                        help='推送微信消息')

    args = parser.parse_args()

    if args.action == 'fetch':
        results = run_fetcher(args)
        for source, items in results.items():
            print(f"\n=== {source.upper()} ===")
            print(f"Found {len(items)} items")
            for item in items[:5]:
                print(f"  {item}")
    elif args.action == 'analyze':
        events = run_analyzer(args)
        print(f"\n共识别 {len(events)} 个催化剂事件")
    elif args.action == 'push':
        events = run_analyzer(args)
        config = Config()
        pusher = WechatPusher(channel=config.WECHAT_CHANNEL, sc_key=config.SCKEY)
        report = CatalystAnalyzer().generate_report(events)
        success = pusher.push("创新药催化剂日报", report)
        print(f"推送{'成功' if success else '失败'}")
    elif args.action == 'schedule':
        from scheduler.jobs import run_scheduler
        run_scheduler()


if __name__ == "__main__":
    main()