"""
Configuration
配置文件
"""
import os


class Config:
    """项目配置"""

    # 微信推送
    WECHAT_CHANNEL = os.getenv("WECHAT_CHANNEL", "serverchan")  # serverchan 或 pushplus
    SCKEY = os.getenv("SCKEY", "")  # Server 酱 SCKEY
    PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "")  # PushPlus token

    # 数据源配置
    FDA_API_KEY = os.getenv("FDA_API_KEY", "")  # FDA API Key (可选)
    PHARMCUBE_TOKEN = os.getenv("PHARMCUBE_TOKEN", "")  # 医药魔方 Token

    # 股票池（关注的医药公司）
    PHARMA_STOCKS = [
        # A股
        "恒瑞医药", "百济神州", "信达生物", "君实生物", "复星医药",
        "石药集团", "中国生物制药", "翰森制药", "和黄医药", "再鼎医药",
        "荣昌生物", "康方生物", "康宁杰瑞", "诺诚健华", "亚盛医药",
        "迪哲医药", "益方生物", "首药控股", "海思科", "泽璟制药",
        # 港股
        "01810", "06160", "01801", "02126", "02359",
        # 美股 ADRs
        "BEKE", "TAL", "BGNE", "ABBV", "MRK"
    ]

    # 关键词过滤（用于公告筛选）
    ANNOUNCEMENT_KEYWORDS = [
        "临床试验", "III期", "NDA", "BLA", "获批", "临床结果",
        "突破性治疗", "优先审评", "上市申请", "临床I期", "临床II期",
        "首例患者入组", "患者入组", "揭盲", "top-line", "临床数据"
    ]

    # 靶点配置
    TARGETS_OF_INTEREST = [
        "PD-1", "PD-L1", "CTLA-4", "LAG-3", "TIGIT", "OX40",
        "CD47", "HER2", "TROP2", "EGFR", "ALK", "ROS1",
        "KRAS", "CDK4/6", "PARP", "ADC", "CAR-T", "TCR-T"
    ]

    # 推送时间
    DAILY_DIGEST_HOUR = 8
    DAILY_DIGEST_MINUTE = 30

    @classmethod
    def get_env(cls):
        """获取环境变量"""
        return {
            "WECHAT_CHANNEL": cls.WECHAT_CHANNEL,
            "SCKEY": "***" if cls.SCKEY else "",
            "PHARMA_STOCKS_COUNT": len(cls.PHARMA_STOCKS)
        }


if __name__ == "__main__":
    config = Config()
    print("Current config:")
    for k, v in config.get_env().items():
        print(f"  {k}: {v}")