from collections import defaultdict
from math import atan2, cos, degrees, hypot, radians, sin
from statistics import mean


COLUMNS = (
    ("position", "机位"), ("facing", "正面方位（°）"), ("count", "有风观测"),
    ("mean", "圆均来风（°）"), ("variance", "圆方差"),
    ("risk", "迎风占比（%）"), ("top", "最大迎风分量（m/s）"),
    ("reading", "最大暴露观测号"), ("decision", "现场动作"),
)


def validate(row):
    if not 0 <= row["facing"] < 360 or not 0 <= row["wind_from"] < 360:
        raise ValueError("请选择 0° 到小于 360° 的方位角")
    if not 0 <= row["speed"] <= 50:
        raise ValueError("风速须在 0–50 m/s")


def wind_direction(angles):
    x, y = mean(cos(radians(angle)) for angle in angles), mean(sin(radians(angle)) for angle in angles)
    length = min(1.0, hypot(x, y))
    if length <= 1e-10:
        return None, 1.0
    return round(degrees(atan2(y, x)) % 360, 2) % 360, round(1.0 - length, 6)


def analyze(rows, parameters):
    limit = parameters.get("head_limit", 4)
    review = parameters.get("review_pct", 40)
    if not rows:
        raise ValueError("请先登记机位风向")
    positions = defaultdict(list)
    seen = set()
    for row in rows:
        validate(row)
        key = row["position"], row["reading"]
        if key in seen:
            raise ValueError(f"重复观测号 {row['reading']}，请核验风向仪记录")
        seen.add(key)
        positions[row["position"]].append(row)
    results = []
    for position, readings in sorted(positions.items()):
        facings = {row["facing"] for row in readings}
        if len(facings) != 1:
            raise ValueError(f"{position} 有多个麦头朝向，请拆为独立机位")
        facing = facings.pop()
        windy = sorted((row for row in readings if row["speed"] > 0), key=lambda row: row["reading"])
        direction, variance = wind_direction([row["wind_from"] for row in windy]) if windy else (None, None)
        projections = [(max(0.0, row["speed"] * cos(radians(row["wind_from"] - facing))), row["reading"])
                       for row in windy]
        strongest, reading = max(projections, default=(0.0, ""))
        risk = 100 * sum(value >= limit for value, _ in projections) / len(windy) if windy else None
        decision = "样本无风，需补观测" if risk is None else "调整朝向后复勘" if risk >= review else "保留机位"
        results.append(dict(position=position, facing=facing, count=len(windy), mean=direction, variance=variance,
                            risk=round(risk, 2) if risk is not None else None,
                            top=round(strongest, 3), reading=reading, decision=decision))
    priority = sum(row["decision"] == "调整朝向后复勘" for row in results)
    return report(f"{priority} 个机位需调整麦头朝向后复勘",
                  f"有风观测中，迎风达到 {limit:g} m/s 的占比达到 {review:g}% 时，安排第二次风噪试录。静风观测保留在台账，不参与方向计算。",
                  [("机位", len(results), "处"), ("有风记录", sum(row["count"] for row in results), "条"),
                   ("迎风复勘", priority, "处")], COLUMNS, results,
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
