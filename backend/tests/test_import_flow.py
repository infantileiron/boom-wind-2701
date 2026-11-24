"""导入原子性、重试幂等与观测快照隔离的集成测试。

在本机缺少 SQLAlchemy 时整体跳过；在后端容器内可用
`docker compose exec -T backend python -m unittest discover -s tests` 运行，
测试进程使用独立的内存 SQLite，不影响演示数据。
"""
import os
import unittest

try:
    import sqlalchemy  # noqa: F401
except ImportError:
    raise unittest.SkipTest("需要后端容器内的依赖环境")

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from types import SimpleNamespace

from src.constants.enums import RunStatus
from src.models.base import Base, Session, engine
from src.models.workflow import AnalysisRun, Observation
from src.services.observations import add_observations, clean_observation, parse_csv
from src.services.runs import create_run, run_pending


def csv_bytes(*lines):
    header = "position,reading,version,facing,wind_from,speed"
    return ("\n".join([header, *lines]) + "\n").encode("utf-8")


class ImportFlowTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.create_all(engine)
        with Session.begin() as db:
            for table in reversed(Base.metadata.sorted_tables):
                db.execute(table.delete())

    def observation_count(self):
        with Session() as db:
            return db.query(Observation).count()

    def import_rows(self, rows):
        with Session.begin() as db:
            return add_observations(db, [clean_observation(row) for row in rows], "tester", "observation.imported")

    def test_interrupted_import_rolls_back_and_retry_is_idempotent(self):
        rows = parse_csv(csv_bytes(
            "堤阶正位,DJ-01,1,350,10,6.3",
            "堤阶正位,DJ-02,1,350,20,6.1",
            "芦沟侧位,LG-01,2,285,300,7.3",
        ))
        with self.assertRaises(RuntimeError):
            with Session.begin() as db:
                add_observations(db, rows, "tester", "observation.imported")
                raise RuntimeError("模拟导入中断")
        self.assertEqual(self.observation_count(), 0)  # 整批不生效

        items = self.import_rows([dict(row) for row in rows])
        self.assertEqual(len(items), 3)  # 中断后重试，整批重新计入一次
        self.assertEqual(self.observation_count(), 3)

        items = self.import_rows([dict(row) for row in rows])
        self.assertEqual(items, [])  # 再次重试不会重复计入
        self.assertEqual(self.observation_count(), 3)

    def test_csv_without_version_column_still_imports(self):
        rows = parse_csv("position,reading,facing,wind_from,speed\n堤阶正位,DJ-01,350,10,6.3\n".encode())
        self.assertNotIn("version", rows[0])
        items = self.import_rows(rows)
        self.assertEqual(len(items), 1)

    def run_once(self, title):
        with Session.begin() as db:
            run = create_run(db, SimpleNamespace(title=title, parameters={}), "analyst")
            run_id = run.id
        with Session.begin() as db:
            self.assertTrue(run_pending(db))
        with Session() as db:
            return db.get(AnalysisRun, run_id)

    def decision_of(self, run, position):
        self.assertEqual(run.status, RunStatus.SUCCEEDED, run.error)
        return next(row for row in run.result["rows"] if row["position"] == position)["decision"]

    def test_recheck_list_uses_readings_booked_at_submission(self):
        self.import_rows([dict(position="台甲", reading="A-01", version=1, facing=0, wind_from=0, speed=10)])
        first = self.run_once("首版快照")
        self.assertEqual(first.sample_size, 1)
        self.assertEqual(self.decision_of(first, "台甲"), "调整朝向后复勘")  # v1 迎风

        # 提交后补发 v2 更正为背风：旧分析保持首版结论，新分析才反映更正
        self.import_rows([dict(position="台甲", reading="A-01", version=2, facing=0, wind_from=180, speed=10)])
        self.assertEqual(self.decision_of(first, "台甲"), "调整朝向后复勘")
        second = self.run_once("更正后快照")
        self.assertEqual(second.sample_size, 2)
        self.assertEqual(self.decision_of(second, "台甲"), "保留机位")

    def test_late_old_version_does_not_override_new_reading(self):
        self.import_rows([dict(position="台甲", reading="A-01", version=2, facing=0, wind_from=180, speed=10)])
        self.import_rows([dict(position="台甲", reading="A-01", version=1, facing=0, wind_from=0, speed=10)])
        run = self.run_once("迟到旧版")
        self.assertEqual(self.decision_of(run, "台甲"), "保留机位")  # 仍以 v2 为准
        self.assertEqual(self.observation_count(), 2)  # 两个版本都留痕台账

    def test_conflicting_latest_version_marks_position_pending(self):
        self.import_rows([
            dict(position="台甲", reading="A-01", version=1, facing=0, wind_from=0, speed=10),
            dict(position="台甲", reading="A-01", version=1, facing=0, wind_from=180, speed=10),
            dict(position="台乙", reading="B-01", version=1, facing=0, wind_from=180, speed=10),
        ])
        run = self.run_once("同版冲突")
        self.assertEqual(self.decision_of(run, "台甲"), "读数待核")
        self.assertEqual(self.decision_of(run, "台乙"), "保留机位")  # 其余机位照常出结论


if __name__ == "__main__":
    unittest.main()
