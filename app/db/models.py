"""
SQLite ORM 数据表定义（Index Monitor - BTC 专用）。

设计原则：
1) 所有表都使用 date 作为主键，格式固定为 YYYY-MM-DD（字符串）
2) date 字段同时加索引，方便按日期范围查询
3) 每个字段都写中文注释，便于后续维护

百分位说明：
- 库中只存 RSI / 恐惧贪婪 / Ahr999 等「原始指标值」
- 多窗口历史百分位由 Streamlit 在内存中动态计算（见 app/ui/chart_history.py）
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
    """BTC RSI 指标表（仅 rsi6 / rsi12 原始值）。"""

    __tablename__ = "btc_rsi"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    rsi6 = Column(Float, nullable=True, comment="6日 RSI 数值")
    rsi12 = Column(Float, nullable=True, comment="12日 RSI 数值")


class BtcFearGreed(Base):
    """BTC 恐惧贪婪指数表（仅 API 原值）。"""

    __tablename__ = "btc_fear_greed"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    value = Column(Integer, nullable=True, comment="当日恐惧贪婪指数值（0-100整数）")
    classification = Column(String(30), nullable=True, comment="文字描述：Extreme Fear/Fear/Neutral/Greed/Extreme Greed")


class BtcAhr999(Base):
    """BTC Ahr999 指标表（仅 ahr999_value 原始值）。"""

    __tablename__ = "btc_ahr999"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    ahr999_value = Column(Float, nullable=True, comment="当日 Ahr999 指标数值")


class Btc4yMa(Base):
    """BTC 4年均线（1458日）及偏离倍数表。"""

    __tablename__ = "btc_4y_ma"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    ma_value = Column(Float, nullable=True, comment="当日 4年移动平均线数值（1458日均线）")
    price_to_4y_ma = Column(Float, nullable=True, comment="当日收盘价 / 4年均线 的倍数")


class Btc200wMa(Base):
    """BTC 200周均线（1400日）及偏离倍数表。"""

    __tablename__ = "btc_200w_ma"

    date = Column(String(10), primary_key=True, index=True, comment="日期，格式 YYYY-MM-DD")
    ma_value = Column(Float, nullable=True, comment="当日 200周移动平均线数值（1400日均线）")
    price_to_200w_ma = Column(Float, nullable=True, comment="当日收盘价 / 200周均线 的倍数")


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


class BtcAlertHistory(Base):
    """实时告警发送历史：按规则记录上次发送时间与总分（用于防重复）。"""

    __tablename__ = "alert_history"

    id = Column(Integer, primary_key=True, index=True)
    rule_code = Column(String(8), nullable=False, unique=True, index=True, comment="因子代码，如 2B")
    last_sent_time = Column(String(19), nullable=False, comment="上次成功发送时间 YYYY-MM-DD HH:MM:SS")
    last_total_score = Column(Integer, nullable=False, comment="上次发送时的抄底分数总分（原始扣分）")

    __table_args__ = (
        UniqueConstraint("rule_code", name="uq_alert_history_rule_code"),
    )


class BtcAlertDedup(Base):
    """实时告警去重（旧表，已由 alert_history 替代，保留兼容）。"""

    __tablename__ = "btc_alert_dedup"

    id = Column(Integer, primary_key=True, index=True)
    rule_code = Column(String(8), nullable=False, index=True, comment="因子代码，如 2B")
    bucket_date = Column(String(10), nullable=False, index=True, comment="去重日期 YYYY-MM-DD")
    sent_at = Column(String(19), nullable=False, comment="发送时间")
    total_score = Column(Integer, nullable=True, comment="推送时实时抄底分数")
    triggered_rules = Column(String(128), nullable=True, comment="推送时全部触发的因子，逗号分隔")

    __table_args__ = (
        UniqueConstraint("rule_code", "bucket_date", name="uq_alert_rule_bucket_date"),
    )


class BtcBottomScore(Base):
    """BTC 抄底分数（每日一条，分数为负向累加，越低越接近抄底信号）。"""

    __tablename__ = "btc_bottom_score"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String(10), nullable=False, unique=True, index=True, comment="日期，格式 YYYY-MM-DD")
    score = Column(Integer, nullable=False, comment="当日抄底分数总分（各因子扣分累加）")
    created_at = Column(String(19), nullable=False, comment="记录写入时间 YYYY-MM-DD HH:MM:SS")


class RealtimeValue(Base):
    """指标实时值缓存表（默认窗口 2Y）。"""

    __tablename__ = "realtime_values"

    id = Column(Integer, primary_key=True, index=True)
    indicator_code = Column(String(64), nullable=False, index=True, comment="指标代码")
    current_value = Column(Float, nullable=True, comment="最新指标值")
    update_time = Column(String(19), nullable=False, comment="更新时间，格式 YYYY-MM-DD HH:MM:SS")
    window = Column(String(16), nullable=False, default="2Y", comment="窗口标识，默认 2Y")

    __table_args__ = (
        UniqueConstraint("indicator_code", "window", name="uq_realtime_indicator_window"),
    )
