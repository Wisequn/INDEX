"""
抄底因子实时告警文案（模板与触发判定，与 calculate_total_score 规则一致）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scheduler.alert_engine import BottomScoreInputs

# 严格使用用户提供的模板（占位符名与 build_format_context 一致）
_ALERT_SCORE_SUFFIX = "，当前BTC下跌因子总分: {total_score} / 100，建议仓位：{suggested_position}%"

ALERT_TEMPLATES: dict[str, str] = {
    "1A": f"🚨 BTC跌至近2年低点！当前价格：{{close}}（当前BTC价格：{{close}}）{_ALERT_SCORE_SUFFIX}",
    "2A": f"⚠️ 短期下跌较快，可能是短期低点。RSI6={{rsi6}}，RSI6%={{rsi6_pct_ui}}（当前BTC价格：{{close}}）{_ALERT_SCORE_SUFFIX}",
    "2B": f"🔥 短期超跌严重，建议抄底！RSI6={{rsi6}}，RSI6%={{rsi6_pct_ui}}（当前BTC价格：{{close}}）{_ALERT_SCORE_SUFFIX}",
    "3A": f"⚠️ 中短期下跌较快，可能是短期低点。RSI12={{rsi12}}，RSI12%={{rsi12_pct_ui}}（当前BTC价格：{{close}}）{_ALERT_SCORE_SUFFIX}",
    "3B": f"🔥 短期超跌严重，具备抄底条件！RSI12={{rsi12}}，RSI12%={{rsi12_pct_ui}}（当前BTC价格：{{close}}）{_ALERT_SCORE_SUFFIX}",
    "4A": f"😰 近期出现恐慌情绪，请继续观测是否具备抄底条件。FearGreed={{value}}（当前BTC价格：{{close}}）{_ALERT_SCORE_SUFFIX}",
    "4B": f"😱 极度恐慌！近两天可能出现抄底低点。FearGreed={{value}}（当前BTC价格：{{close}}）{_ALERT_SCORE_SUFFIX}",
    "5A": f"📉 BTC具备定投条件了，可以开始少量定投。Ahr999={{ahr999_value}}（当前BTC价格：{{close}}）{_ALERT_SCORE_SUFFIX}",
    "5B": f"📉 进入熊市周期，可以抄底或持续定投。Ahr999={{ahr999_value}}（当前BTC价格：{{close}}）{_ALERT_SCORE_SUFFIX}",
    "5C": f"🔥 相信跌了很多了，可以满仓了吧？Ahr999={{ahr999_value}}（当前BTC价格：{{close}}）{_ALERT_SCORE_SUFFIX}",
    "6A": f"⚾ BTC进入击球区了！价格/4年均线={{price_to_4y_ma}}（当前BTC价格：{{close}}）{_ALERT_SCORE_SUFFIX}",
    "6B": f"🔥 可以满仓了吧？价格/4年均线={{price_to_4y_ma}}（当前BTC价格：{{close}}）{_ALERT_SCORE_SUFFIX}",
}

DAILY_BRIEFING_TEMPLATE = """Index 每日早报 ({report_date} 09:00)
BTC价格: {close}
RSI6: {rsi6} (RSI6%: {rsi6_pct_ui})
RSI12: {rsi12} (RSI12%: {rsi12_pct_ui})
FearGreed: {value} (FearGreed%: {fg_pct_ui})
Ahr999: {ahr999_value} (Ahr999%: {ahr999_pct_ui})
价格/4年均线: {price_to_4y_ma}
价格/200周均线: {price_to_200w_ma}
当前BTC下跌因子总分: {total_score} / 100"""


@dataclass(frozen=True)
class BriefingSnapshot(BottomScoreInputs):
    """早报额外字段。"""

    price_to_200w_ma: float | None = None
    report_date: str = ""
    total_score: int = 0


def _hit_1a(inp: BottomScoreInputs) -> bool:
    return (
        inp.close is not None
        and inp.close_2y_low is not None
        and inp.close == inp.close_2y_low
    )


def _hit_2b(inp: BottomScoreInputs) -> bool:
    return (
        inp.rsi6 is not None
        and inp.rsi6_pct_ui is not None
        and inp.rsi6 < 12
        and inp.rsi6_pct_ui < 1
    )


def _hit_2a(inp: BottomScoreInputs) -> bool:
    return (
        inp.rsi6 is not None
        and inp.rsi6_pct_ui is not None
        and inp.rsi6 < 12
        and inp.rsi6_pct_ui < 5
    )


def _hit_3b(inp: BottomScoreInputs) -> bool:
    return (
        inp.rsi12 is not None
        and inp.rsi12_pct_ui is not None
        and inp.rsi12 < 15
        and inp.rsi12_pct_ui < 1
    )


def _hit_3a(inp: BottomScoreInputs) -> bool:
    return (
        inp.rsi12 is not None
        and inp.rsi12_pct_ui is not None
        and inp.rsi12 < 25
        and inp.rsi12_pct_ui < 5
    )


def _hit_4b(inp: BottomScoreInputs) -> bool:
    return (
        inp.value is not None
        and inp.fg_pct_ui is not None
        and inp.value <= 8
        and inp.fg_pct_ui < 0.5
    )


def _hit_4a(inp: BottomScoreInputs) -> bool:
    return (
        inp.value is not None
        and inp.fg_pct_ui is not None
        and inp.value <= 12
        and inp.fg_pct_ui < 2
    )


def _hit_5b(inp: BottomScoreInputs) -> bool:
    return (
        inp.ahr999_value is not None
        and inp.ahr999_pct_ui is not None
        and inp.ahr999_value < 0.45
        and inp.ahr999_pct_ui < 3
    )


def _hit_5a(inp: BottomScoreInputs) -> bool:
    return (
        inp.ahr999_value is not None
        and inp.ahr999_pct_ui is not None
        and inp.ahr999_value < 0.85
        and inp.ahr999_pct_ui < 3
    )


def _hit_5c(inp: BottomScoreInputs) -> bool:
    return inp.ahr999_value is not None and inp.ahr999_value < 0.35


def _hit_6b(inp: BottomScoreInputs) -> bool:
    return inp.price_to_4y_ma is not None and inp.price_to_4y_ma < 0.8


def _hit_6a(inp: BottomScoreInputs) -> bool:
    return inp.price_to_4y_ma is not None and inp.price_to_4y_ma < 1.1


def evaluate_triggered_alert_rules(inp: BottomScoreInputs) -> list[str]:
    """
    返回应推送的规则代码列表（A/B 对只取 B 或 A；5C 可与 5A/5B 同时出现）。
    """
    rules: list[str] = []
    if _hit_1a(inp):
        rules.append("1A")
    if _hit_2b(inp):
        rules.append("2B")
    elif _hit_2a(inp):
        rules.append("2A")
    if _hit_3b(inp):
        rules.append("3B")
    elif _hit_3a(inp):
        rules.append("3A")
    if _hit_4b(inp):
        rules.append("4B")
    elif _hit_4a(inp):
        rules.append("4A")
    if _hit_5c(inp):
        rules.append("5C")
    elif _hit_5b(inp):
        rules.append("5B")
    elif _hit_5a(inp):
        rules.append("5A")
    if _hit_6b(inp):
        rules.append("6B")
    elif _hit_6a(inp):
        rules.append("6A")
    return rules


def build_format_context(
    inp: BottomScoreInputs,
    *,
    score_meta: dict[str, int] | None = None,
    total_score: int | None = None,
) -> dict[str, str]:
    """将数值格式化为模板字符串（总分/仓位使用归一化结果）。"""
    from scheduler.alert_engine import calculate_normalized_score_and_position

    def _f(v: float | None, nd: int = 4) -> str:
        if v is None:
            return "N/A"
        if nd == 0:
            return f"{v:,.0f}"
        return f"{v:.{nd}f}"

    if score_meta is None:
        raw = total_score if total_score is not None else getattr(inp, "total_score", None)
        if raw is not None:
            score_meta = calculate_normalized_score_and_position(raw_score=int(raw))
        else:
            score_meta = calculate_normalized_score_and_position(inputs=inp)

    norm = int(score_meta["normalized_score"])
    pos = int(score_meta["suggested_position"])

    return {
        "close": _f(inp.close, 2),
        "rsi6": _f(inp.rsi6, 2),
        "rsi6_pct_ui": _f(inp.rsi6_pct_ui, 2),
        "rsi12": _f(inp.rsi12, 2),
        "rsi12_pct_ui": _f(inp.rsi12_pct_ui, 2),
        "value": _f(inp.value, 0),
        "fg_pct_ui": _f(inp.fg_pct_ui, 2),
        "ahr999_value": _f(inp.ahr999_value, 4),
        "ahr999_pct_ui": _f(inp.ahr999_pct_ui, 2),
        "price_to_4y_ma": _f(inp.price_to_4y_ma, 4),
        "price_to_200w_ma": _f(getattr(inp, "price_to_200w_ma", None), 4),
        "total_score": str(norm),
        "suggested_position": str(pos),
        "report_date": str(getattr(inp, "report_date", "")),
    }


def format_alert_message(
    rule_code: str,
    inp: BottomScoreInputs,
    *,
    score_meta: dict[str, int] | None = None,
    total_score: int | None = None,
) -> str:
    """生成实时告警文案；score_meta 含 raw_score / normalized_score / suggested_position。"""
    template = ALERT_TEMPLATES[rule_code]
    return template.format(
        **build_format_context(inp, score_meta=score_meta, total_score=total_score)
    )


def format_daily_briefing(brief: BriefingSnapshot) -> str:
    return DAILY_BRIEFING_TEMPLATE.format(**build_format_context(brief))
