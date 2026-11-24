import unittest

from src.domain.analysis import KEEP, NO_WIND, PENDING, RECHECK, analyze, merge_readings

PARAMS = {"head_limit": 4, "review_pct": 40}


def obs(position, reading, facing, wind_from, speed, version=None):
    row = dict(position=position, reading=reading, facing=facing, wind_from=wind_from, speed=speed)
    if version is not None:
        row["version"] = version
    return row


def row_of(result, position):
    return next(row for row in result["rows"] if row["position"] == position)


class MergeReadingsTest(unittest.TestCase):
    def test_keeps_highest_version(self):
        positions = merge_readings([
            obs("台甲", "A-01", 0, 0, 10, version=1),
            obs("台甲", "A-01", 0, 90, 10, version=3),
            obs("台甲", "A-01", 0, 180, 10, version=2),
        ])
        base, conflicts = positions["台甲"]
        self.assertEqual([row["wind_from"] for row in base], [90])
        self.assertEqual(conflicts, [])

    def test_missing_version_counts_as_one(self):
        positions = merge_readings([
            obs("台甲", "A-01", 0, 0, 10),
            obs("台甲", "A-01", 0, 180, 10),
        ])
        base, conflicts = positions["台甲"]
        self.assertEqual(base, [])
        self.assertEqual({variant["wind_from"] for variant in conflicts[0]}, {0, 180})

    def test_identical_rows_collapse(self):
        positions = merge_readings([obs("台甲", "A-01", 0, 0, 10), obs("台甲", "A-01", 0, 0, 10)])
        base, conflicts = positions["台甲"]
        self.assertEqual(len(base), 1)
        self.assertEqual(conflicts, [])

    def test_same_reading_same_values_different_version_is_not_conflict(self):
        positions = merge_readings([
            obs("台甲", "A-01", 0, 0, 10, version=1),
            obs("台甲", "A-01", 0, 0, 10, version=2),
        ])
        base, conflicts = positions["台甲"]
        self.assertEqual(len(base), 1)
        self.assertEqual(conflicts, [])


class AnalyzeMergeTest(unittest.TestCase):
    def test_late_old_version_does_not_override_new_reading(self):
        result = analyze([
            obs("台甲", "A-01", 0, 180, 10, version=2),  # 新版读数：背风
            obs("台甲", "A-01", 0, 0, 10, version=1),    # 迟到的旧版：迎风
        ], PARAMS)
        row = row_of(result, "台甲")
        self.assertEqual(row["decision"], KEEP)
        self.assertEqual(row["count"], 1)
        self.assertEqual(row["risk"], 0)

    def test_divergent_conflict_marks_only_that_position_pending(self):
        result = analyze([
            obs("台甲", "A-01", 0, 0, 10),    # 变体一：迎风 10 m/s
            obs("台甲", "A-01", 0, 180, 10),  # 同一最新版变体二：背风
            obs("台乙", "B-01", 0, 180, 10),  # 另一机位不受影响
        ], PARAMS)
        pending = row_of(result, "台甲")
        self.assertEqual(pending["decision"], PENDING)
        self.assertIsNone(pending["risk"])
        self.assertIsNone(pending["count"])
        self.assertEqual(row_of(result, "台乙")["decision"], KEEP)
        self.assertIn("读数待核", result["headline"])
        self.assertIn(("读数待核", 1, "处"),
                      [(item["label"], item["value"], item["unit"]) for item in result["metrics"]])

    def test_conflict_straddling_review_threshold(self):
        result = analyze([
            obs("台甲", "A-01", 0, 0, 10),
            obs("台甲", "A-02", 0, 180, 10),
            obs("台甲", "A-03", 0, 180, 10),
            obs("台甲", "A-03", 0, 0, 10),  # 同版冲突：占比在 1/3 与 2/3 之间摆动
        ], PARAMS)
        self.assertEqual(row_of(result, "台甲")["decision"], PENDING)

    def test_consistent_conflict_keeps_conclusion(self):
        result = analyze([
            obs("台甲", "A-01", 0, 0, 10),
            obs("台甲", "A-01", 0, 20, 10),  # 两个变体都迎风达线
        ], PARAMS)
        row = row_of(result, "台甲")
        self.assertEqual(row["decision"], RECHECK)
        self.assertEqual(row["risk"], 100)

    def test_consistent_conflict_reports_boundary_risk(self):
        result = analyze([
            obs("台甲", "A-01", 0, 0, 10),
            obs("台甲", "A-02", 0, 0, 10),
            obs("台甲", "A-03", 0, 0, 10),
            obs("台甲", "A-03", 0, 180, 10),  # 结论同为复勘，占比取靠近判读线的 2/3
        ], PARAMS)
        row = row_of(result, "台甲")
        self.assertEqual(row["decision"], RECHECK)
        self.assertEqual(row["risk"], 66.67)

    def test_calm_variant_conflict_diverges(self):
        result = analyze([
            obs("台甲", "A-01", 0, 0, 0),   # 变体一：静风
            obs("台甲", "A-01", 0, 0, 10),  # 变体二：迎风达线
        ], PARAMS)
        self.assertEqual(row_of(result, "台甲")["decision"], PENDING)

    def test_facing_conflict_marks_pending(self):
        result = analyze([
            obs("台甲", "A-01", 0, 0, 10),
            obs("台甲", "A-01", 90, 0, 10),
        ], PARAMS)
        row = row_of(result, "台甲")
        self.assertEqual(row["decision"], PENDING)
        self.assertIsNone(row["facing"])

    def test_combination_cap_marks_pending(self):
        rows = []
        for index in range(8):  # 2**8 = 256 种组合，超出枚举上限
            rows.append(obs("台甲", f"A-{index:02d}", 0, 0, 10))
            rows.append(obs("台甲", f"A-{index:02d}", 0, 180, 10))
        self.assertEqual(row_of(analyze(rows, PARAMS), "台甲")["decision"], PENDING)


class AnalyzeRegressionTest(unittest.TestCase):
    def test_multiple_facings_still_raise(self):
        with self.assertRaises(ValueError):
            analyze([obs("台甲", "A-01", 0, 0, 10), obs("台甲", "A-02", 90, 0, 10)], PARAMS)

    def test_empty_rows_raise(self):
        with self.assertRaises(ValueError):
            analyze([], PARAMS)

    def test_invalid_version_raises(self):
        with self.assertRaises(ValueError):
            analyze([obs("台甲", "A-01", 0, 0, 10, version=0)], PARAMS)

    def test_calm_position_unchanged(self):
        result = analyze([obs("台甲", "A-01", 0, 0, 0)], PARAMS)
        self.assertEqual(row_of(result, "台甲")["decision"], NO_WIND)

    def test_seed_positions_compute(self):
        from src.domain.analysis import seed
        result = analyze(seed(), PARAMS)
        self.assertEqual(len(result["rows"]), 4)
        self.assertFalse(any(row["decision"] == PENDING for row in result["rows"]))


if __name__ == "__main__":
    unittest.main()
