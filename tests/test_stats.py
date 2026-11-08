"""业务统计接口的测试：只验证聚合口径与项目隔离，不依赖真实推理。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TMP = os.environ.setdefault("SHIP_VAR_DIR",
                             str(Path(tempfile.gettempdir()) / f"ship-test-{os.getpid()}"))
os.environ.setdefault("SHIP_DB_PATH", str(Path(_TMP) / "test.db"))
os.environ.setdefault("SHIP_DEVICE", "cpu")
os.environ.setdefault("SHIP_SEED_PASSWORD", "test-password-1234")
os.environ.setdefault("SHIP_MAX_ACTIVE_TASKS", "1")
os.environ.setdefault("SHIP_CHUNK_SIZE", "65536")
os.environ.setdefault("SHIP_WEIGHTS", str(ROOT / "models/yolov8l-obb-dota-v1-80e/best.pt"))
sys.path.insert(0, str(ROOT))

from backend import db, stats  # noqa: E402


class StatsAggregationTests(unittest.TestCase):
    """直接对聚合函数做单元测试（不经过 HTTP，数据手工插入）。"""

    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.project = "harbor-a"
        cls.task = "tsk_stats_probe"
        with db.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tasks(id,project_id,image_id,created_by,status,attempt,"
                "tiles_total,tiles_done,temp_count,final_count,parameters,cancel_requested,"
                "created_at,started_at,finished_at) VALUES(?,?,?,?,'done',1,12,12,7,7,'{}',0,?,?,?)",
                (cls.task, cls.project, "img_stats", "usr_owner",
                 "2026-11-04T10:00:00+00:00", "2026-11-04T10:00:01+00:00",
                 "2026-11-04T10:00:09+00:00"),
            )
            for index, (state, origin) in enumerate(
                    [("confirmed", "model"), ("modified", "model"), ("pending", "model"),
                     ("deleted", "model"), ("confirmed", "ai"), ("confirmed", "manual")], start=1):
                conn.execute(
                    "INSERT OR REPLACE INTO draft_detections(task_id,detection_id,polygon,class_id,"
                    "class_name,confidence,source_tile_id,review_state,origin,note,ai_candidate_id)"
                    " VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL)",
                    (cls.task, index, "[[0,0],[1,0],[1,1],[0,1]]", 1, "ship",
                     0.8 if origin == "model" else None, 1, state, origin),
                )
            conn.execute(
                "INSERT OR REPLACE INTO versions(task_id,label,project_id,author,created_at,"
                "readonly,summary,counts,detections) VALUES(?,?,?,?,?,1,'{}','{}','[]')",
                (cls.task, "v0", cls.project, "model", "2026-11-04T10:00:10+00:00"),
            )
            for candidate_id, status, source in (
                    ("cand_ok", "accepted", "detect"),
                    ("cand_no", "cancelled", "detect"),
                    ("cand_wait", "preview", "contour")):
                conn.execute(
                    "INSERT OR REPLACE INTO candidates(id,image_id,project_id,user_id,status,"
                    "polygon,source,width,length,quality,reason,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (candidate_id, "img_stats", cls.project, "usr_owner", status,
                     "[[0,0],[1,0],[1,1],[0,1]]", source, 10, 40, 0.7, "", "2026-11-04T10:01:00+00:00"),
                )

    def test_project_stats_shape_and_values(self):
        payload = stats.project_stats(self.project, "2026-11-01T00:00:00+00:00",
                                      "2026-11-30T00:00:00+00:00")
        self.assertIn("tasks", payload)
        self.assertGreaterEqual(payload["tasks"]["total"], 1)
        self.assertGreaterEqual(payload["tasks"]["tiles_total"], 12)
        self.assertIsNotNone(payload["tasks"]["duration_ms"]["p50"])
        self.assertGreaterEqual(payload["tasks"]["duration_ms"]["samples"], 1)

        review = payload["review"]
        self.assertGreaterEqual(review["detections"], 6)
        self.assertIn("completion_rate", review)
        # 口径：count 是候选数，不是确认数——文档里也要这么写
        self.assertIn("note", review)

        ai = payload["ai"]
        self.assertGreaterEqual(ai["proposed"], 3)
        self.assertEqual(ai["accepted"], 1)      # 只有 accepted 计入
        self.assertEqual(ai["rejected"], 1)      # preview 不计入已处理
        self.assertEqual(ai["accept_rate"], 0.5)
        self.assertIn("detect", ai["by_source"])
        self.assertIn("contour", ai["by_source"])

    def test_time_range_filters_out_everything(self):
        payload = stats.project_stats(self.project, "2020-01-01T00:00:00+00:00",
                                      "2020-01-02T00:00:00+00:00")
        self.assertEqual(payload["tasks"]["total"], 0)
        self.assertIsNone(payload["tasks"]["success_rate"])
        self.assertIsNone(payload["tasks"]["duration_ms"]["p50"])

    def test_task_stats(self):
        row = db.one("SELECT * FROM tasks WHERE id=?", (self.task,))
        payload = stats.task_stats(row)
        self.assertEqual(payload["task"]["status"], "done")
        self.assertEqual(payload["progress"]["tiles_total"], 12)
        self.assertEqual(payload["detections"]["total"], 6)
        self.assertEqual(payload["review"]["pending"], 1)
        self.assertEqual(payload["ai"]["accepted"], 1)
        self.assertIn("v0", payload["versions"]["labels"])
        self.assertIsNone(payload["timing"]["predict_call_ms"])   # 没结果文件时如实为 null


if __name__ == "__main__":
    unittest.main()
