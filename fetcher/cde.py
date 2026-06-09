"""
CDE / NMPA Drug Approvals Fetcher
从 CDE 药品审评中心获取中国药品审评审批动态
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import List, Dict
import json


class CDEFetcher:
    """CDE/NMPA 审批数据获取"""

    CDE_API = "https://www.cde.org.cn"

    def get_drug_approval(self, page: int = 1) -> List[Dict]:
        """
        获取化学药品审批数据
        CDE 首页 > 审评审批 > 优先审评/突破性治疗
        """
        results = []

        # CDE 优先审评公示
        url = f"{self.CDE_API}/centerDrug/getDrugReform"
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        data = {
            "pageNum": page,
            "pageSize": 20,
            "releaseTimeStart": "",
            "releaseTimeEnd": "",
            "drugName": "",
            "company": ""
        }

        try:
            resp = requests.post(url, json=data, headers=headers, timeout=30)
            resp.raise_for_status()
            result = resp.json()

            if result.get('code') == 200:
                items = result.get('data', {}).get('list', [])
                for item in items:
                    results.append({
                        'drug_name': item.get('drugName', ''),
                        'company': item.get('company', ''),
                        'approval_date': item.get('releaseTime', ''),
                        'indication': item.get('indication', ''),
                        'drug_type': item.get('drugType', ''),
                        'source': 'CDE NMPA'
                    })
        except Exception as e:
            print(f"CDE API Error: {e}")

        return results

    def get_clinical_trials(self, drug_name: str = None) -> List[Dict]:
        """
        获取临床试验默示许可
        """
        results = []
        url = f"{self.CDE_API}/centerCT/getCTList"
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        data = {
            "pageNum": 1,
            "pageSize": 20,
            "drugName": drug_name or '',
            "company": ""
        }

        try:
            resp = requests.post(url, json=data, headers=headers, timeout=30)
            resp.raise_for_status()
            result = resp.json()

            if result.get('code') == 200:
                items = result.get('data', {}).get('list', [])
                for item in items:
                    results.append({
                        'drug_name': item.get('drugName', ''),
                        'company': item.get('company', ''),
                        'indication': item.get('indication', ''),
                        'clinical_stage': item.get('stage', ''),
                        'source': 'CDE Clinical'
                    })
        except Exception as e:
            print(f"CDE Clinical API Error: {e}")

        return results


if __name__ == "__main__":
    fetcher = CDEFetcher()
    approvals = fetcher.get_drug_approval()
    print(f"Found {len(approvals)} CDE approvals")
    for a in approvals[:5]:
        print(a)