"""
SQLite ORM 数据表定义（Index Monitor - BTC 专用）。

设计原则：
1) 所有表都使用 date 作为主键，格式固定为 YYYY-MM-DD（字符串）
2) date 字段同时加索引，方便按日期范围查询
3) 每个字段都写中文注释，便于后续维护
"""

from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON

from app.db.database import Base


class BtcPrice(Base):
    """BTC 日线价格表。"""

    __tablename__ = "btc_price"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    open = Column(Float, nullable=False, comment="当日开盘价")
    high = Column(Float, nullable=False, comment="当日最高价")
    low = Column(Float, nullable=False, comment="当日最低价")
    close = Column(Float, nullable=False, comment="当日收盘价")
    volume = Column(Float, nullable=True, comment="当日成交量")


class BtcRsi(Base):
    """BTC RSI 指标表。"""

    __tablename__ = "btc_rsi"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    rsi6 = Column(Float, nullable=True, comment="6日 RSI 数值")
    rsi12 = Column(Float, nullable=True, comment="12日 RSI 数值")


class BtcRsiPercentile(Base):
    """BTC RSI 历史百分位表。"""

    __tablename__ = "btc_rsi_percentile"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    rsi6_pct_1y = Column(Float, nullable=True, comment="RSI6 在过去1年历史数据中的百分位（0-100）")
    rsi6_pct_2y = Column(Float, nullable=True, comment="RSI6 在过去2年历史数据中的百分位（0-100）")
    rsi6_pct_3y = Column(Float, nullable=True, comment="RSI6 在过去3年历史数据中的百分位（0-100）")
    rsi6_pct_4y = Column(Float, nullable=True, comment="RSI6 在过去4年历史数据中的百分位（0-100）")
    rsi6_pct_all = Column(Float, nullable=True, comment="RSI6 在全部历史数据中的百分位（0-100）")
    rsi12_pct_1y = Column(Float, nullable=True, comment="RSI12 在过去1年历史数据中的百分位（0-100）")
    rsi12_pct_2y = Column(Float, nullable=True, comment="RSI12 在过去2年历史数据中的百分位（0-100）")
    rsi12_pct_3y = Column(Float, nullable=True, comment="RSI12 在过去3年历史数据中的百分位（0-100）")
    rsi12_pct_4y = Column(Float, nullable=True, comment="RSI12 在过去4年历史数据中的百分位（0-100）")
    rsi12_pct_all = Column(Float, nullable=True, comment="RSI12 在全部历史数据中的百分位（0-100）")


class BtcFearGreed(Base):
    """BTC 恐惧贪婪指数表。"""

    __tablename__ = "btc_fear_greed"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    value = Column(Integer, nullable=True, comment="当日恐惧贪婪指数值（0-100整数）")
    classification = Column(String(30), nullable=True, comment="文字描述：Extreme Fear/Fear/Neutral/Greed/Extreme Greed")
    fg_pct_1y = Column(Float, nullable=True, comment="value 在过去1年历史数据中的百分位（0-100）")
    fg_pct_2y = Column(Float, nullable=True, comment="value 在过去2年历史数据中的百分位（0-100）")
    fg_pct_3y = Column(Float, nullable=True, comment="value 在过去3年历史数据中的百分位（0-100）")
    fg_pct_4y = Column(Float, nullable=True, comment="value 在过去4年历史数据中的百分位（0-100）")
    fg_pct_all = Column(Float, nullable=True, comment="value 在全部历史数据中的百分位（0-100）")


class BtcAhr999(Base):
    """BTC Ahr999 指标表。"""

    __tablename__ = "btc_ahr999"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    ahr999_value = Column(Float, nullable=True, comment="当日 Ahr999 指标数值")
    ahr999_pct_1y = Column(Float, nullable=True, comment="ahr999_value 在过去1年历史数据中的百分位（0-100）")
    ahr999_pct_2y = Column(Float, nullable=True, comment="ahr999_value 在过去2年历史数据中的百分位（0-100）")
    ahr999_pct_3y = Column(Float, nullable=True, comment="ahr999_value 在过去3年历史数据中的百分位（0-100）")
    ahr999_pct_4y = Column(Float, nullable=True, comment="ahr999_value 在过去4年历史数据中的百分位（0-100）")
    ahr999_pct_all = Column(Float, nullable=True, comment="ahr999_value 在全部历史数据中的百分位（0-100）")


class Btc4yMa(Base):
    """BTC 4年均线（1458日）及偏离倍数表。"""

    __tablename__ = "btc_4y_ma"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    ma_value = Column(Float, nullable=True, comment="当日 4年移动平均线数值（1458日均线）")
    price_to_4y_ma = Column(Float, nullable=True, comment="当日收盘价 / 4年均线 的倍数")
    p4yma_pct_1y = Column(Float, nullable=True, comment="price_to_4y_ma 在过去1年历史数据中的百分位（0-100）")
    p4yma_pct_4y = Column(Float, nullable=True, comment="price_to_4y_ma 在过去4年历史数据中的百分位（0-100）")
    p4yma_pct_all = Column(Float, nullable=True, comment="price_to_4y_ma 在全部历史数据中的百分位（0-100）")


class Btc200wMa(Base):
    """BTC 200周均线（1400日）及偏离倍数表。"""

    __tablename__ = "btc_200w_ma"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    ma_value = Column(Float, nullable=True, comment="当日 200周移动平均线数值（1400日均线）")
    price_to_200w_ma = Column(Float, nullable=True, comment="当日收盘价 / 200周均线 的倍数")
    p200wma_pct_1y = Column(Float, nullable=True, comment="price_to_200w_ma 在过去1年历史数据中的百分位（0-100）")
    p200wma_pct_4y = Column(Float, nullable=True, comment="price_to_200w_ma 在过去4年历史数据中的百分位（0-100）")
    p200wma_pct_all = Column(Float, nullable=True, comment="price_to_200w_ma 在全部历史数据中的百分位（0-100）")


class BtcRiskScore(Base):
    """BTC 综合风险评分表。"""

    __tablename__ = "btc_risk_score"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    score = Column(Float, nullable=True, comment="当日综合风险评分（0-100，越高风险越大）")
    components = Column(JSON, nullable=True, comment="JSON格式，存各子指标贡献值（算法后续完善，暂时可为空）")


class MarketData(Base):
    """
    兼容旧版 Streamlit 页面使用的通用市场数据表。

    说明：
    - 这个模型用于兼容 app/ui/main.py + app/services/data_service.py 的旧逻辑
    - 不影响你当前 BTC 专用表结构（btc_*）
    """

    __tablename__ = "market_data"

    id = Column(Integer, primary_key=True, index=True)
    market = Column(String(20), nullable=False, index=True, comment="市场类型（crypto/us/cn）")
    symbol = Column(String(50), nullable=False, index=True, comment="标的代码（BTC-USD/AAPL/000001.SZ）")
    timestamp = Column(DateTime, nullable=False, index=True, comment="数据时间戳")

    open = Column(Float, nullable=True, comment="开盘价")
    high = Column(Float, nullable=True, comment="最高价")
    low = Column(Float, nullable=True, comment="最低价")
    close = Column(Float, nullable=True, comment="收盘价")
    volume = Column(Float, nullable=True, comment="成交量")

    __table_args__ = (
        UniqueConstraint("market", "symbol", "timestamp", name="uq_market_symbol_timestamp"),
    )
