from collections import defaultdict
from itertools import product
from math import atan2, cos, degrees, hypot, radians, sin
from statistics import mean


COLUMNS = (
    ("position", "机位"), ("facing", "正面方位（°）"), ("count", "有风观测"),
    ("mean", "圆均来风（°）"), ("variance", "圆方差"),
    ("risk", "迎风占比（%）"), ("top", "最大迎风分量（m/s）"),
    ("reading", "最大暴露观测号"), ("decision", "现场动作"),
)

RECHECK = "调整朝向后复勘"
KEEP = "保留机位"
NO_WIND = "样本无风，需补观测"
PENDING = "读数待核"

# 同一机位冲突变体可枚举的组合上限，超出时无法逐一核算，直接标为读数待核
MAX_COMBINATIONS = 128


def validate(row):
    if not 0 <= row["facing"] < 360 or not 0 <= row["wind_from"] < 360:
        raise ValueError("请选择 0° 到小于 360° 的方位角")
    if not 0 <= row["speed"] <= 50:
        raise ValueError("风速须在 0–50 m/s")
    if not 1 <= row.get("version", 1) <= 9999:
        raise ValueError("补发版本号须在 1–9999")


def wind_direction(angles):
    x, y = mean(cos(radians(angle)) for angle in angles), mean(sin(radians(angle)) for angle in angles)
    length = min(1.0, hypot(x, y))
    if length <= 1e-10:
        return None, 1.0
    return round(degrees(atan2(y, x)) % 360, 2) % 360, round(1.0 - length, 6)


def merge_readings(rows):
    """补发记录按机位和观测号合并：同一观测号只保留最高版本，迟到的旧版不顶掉新版读数。
    返回 {机位: (无冲突读数列表, [同一最新版的冲突变体组, ...])}，版本缺省按 1 计。"""
    groups = defaultdict(list)
    for row in rows:
        groups[row["position"], row["reading"]].append(row)
    positions = {}
    for (position, _reading), group in groups.items():
        top = max(row.get("version", 1) for row in group)
        variants = {}
        for row in group:
            if row.get("version", 1) == top:
                variants.setdefault((row["facing"], row["wind_from"], row["speed"]), row)
        base, conflicts = positions.setdefault(position, ([], []))
        if len(variants) == 1:
            base.append(next(iter(variants.values())))
        else:
            conflicts.append(list(variants.values()))
    return positions


def compute_position(position, rows, limit, review):
    """按原口径核算一个机位，rows 为合并后无冲突的读数。"""
    facings = {row["facing"] for row in rows}
    if len(facings) != 1:
        raise ValueError(f"{position} 有多个麦头朝向，请拆为独立机位")
    facing = facings.pop()
    windy = sorted((row for row in rows if row["speed"] > 0), key=lambda row: row["reading"])
    direction, variance = wind_direction([row["wind_from"] for row in windy]) if windy else (None, None)
    projections = [(max(0.0, row["speed"] * cos(radians(row["wind_from"] - facing))), row["reading"])
                   for row in windy]
    strongest, reading = max(projections, default=(0.0, ""))
    risk = 100 * sum(value >= limit for value, _ in projections) / len(windy) if windy else None
    decision = NO_WIND if risk is None else RECHECK if risk >= review else KEEP
    return dict(position=position, facing=facing, count=len(windy), mean=direction, variance=variance,
                risk=round(risk, 2) if risk is not None else None,
                top=round(strongest, 3), reading=reading, decision=decision)


def resolve_conflicts(position, base, conflicts, limit, review):
    """同一最新版存在不同读数时，按原迎风占比口径枚举各可能组合分别核算。
    复勘结论一致则照常出结论（占比取最靠近判读线的组合）；结论有分歧、朝向不一致
    或组合过多无法逐一核算时，仅把该机位标为读数待核。"""
    facings = {row["facing"] for row in base} | {row["facing"] for group in conflicts for row in group}
    combinations = 1
    for group in conflicts:
        combinations *= len(group)
    if len(facings) == 1 and combinations <= MAX_COMBINATIONS:
        outcomes = [compute_position(position, base + list(picks), limit, review)
                    for picks in product(*conflicts)]
        decisions = {row["decision"] for row in outcomes}
        if len(decisions) == 1:
            decision = decisions.pop()
            if decision == RECHECK:
                return min(outcomes, key=lambda row: row["risk"])
            if decision == KEEP:
                return max(outcomes, key=lambda row: row["risk"])
            return outcomes[0]
    return dict(position=position, facing=facings.pop() if len(facings) == 1 else None,
                count=None, mean=None, variance=None, risk=None, top=None, reading="",
                decision=PENDING)


def analyze(rows, parameters):
    limit = parameters.get("head_limit", 4)
    review = parameters.get("review_pct", 40)
    if not rows:
        raise ValueError("请先登记机位风向")
    for row in rows:
        validate(row)
    positions = merge_readings(rows)
    results = []
    for position in sorted(positions):
        base, conflicts = positions[position]
        if conflicts:
            results.append(resolve_conflicts(position, base, conflicts, limit, review))
        else:
            results.append(compute_position(position, base, limit, review))
    priority = sum(row["decision"] == RECHECK for row in results)
    pending = sum(row["decision"] == PENDING for row in results)
    headline = f"{priority} 个机位需调整麦头朝向后复勘"
    metrics = [("机位", len(results), "处"),
               ("有风记录", sum(row["count"] for row in results if row["count"] is not None), "条"),
               ("迎风复勘", priority, "处")]
    if pending:
        headline += f"，{pending} 个机位读数待核"
        metrics.append(("读数待核", pending, "处"))
    return report(headline,
                  f"有风观测中，迎风达到 {limit:g} m/s 的占比达到 {review:g}% 时，安排第二次风噪试录。静风观测保留在台账，不参与方向计算。"
                  "补发记录按机位与观测号合并，仅最高版本参与计算；同一最新版本读数不一致且可能改变复勘结论的机位标为读数待核。",
                  metrics, COLUMNS, results,
                  "有风观测中的迎风占比", [(row["position"], row["risk"]) for row in results if row["risk"] is not None], "%")


def seed():
    sites = [
        ("堤阶正位", 350, 355, 6.3, "DJ"),
        ("芦沟侧位", 285, 40, 6.4, "LG"),
        ("承台背位", 170, 10, 5.1, "CT"),
        ("仓沿转角", 270, 260, 4.9, "CY"),
    ]
    return [dict(position=position, reading=f"{prefix}-{index+1:02d}", facing=facing,
                 wind_from=(bearing + (index * 17 % 37) - 18) % 360,
                 speed=round(speed + (index * 13 % 19 - 8) / 5, 1))
            for position, facing, bearing, speed, prefix in sites for index in range(12)]


def report(headline, summary, metrics, columns, rows, chart_label, chart_values, unit=""):
    return {
        "headline": headline, "summary": summary,
        "metrics": [{"label": label, "value": value, "unit": suffix} for label, value, suffix in metrics],
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": [{key: row.get(key) for key, _ in columns} for row in rows],
        "chart": {"label": chart_label, "unit": unit,
                  "points": [{"label": label, "value": value} for label, value in chart_values]},
    }
