"""后端冒烟测试（不依赖 AI 模型）。用于快速验证模块可导入、核心流程可运行。"""
import os
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# 使用临时数据目录，避免污染真实数据。
# 注意：paths._appdata() 在 Windows 上优先读 LOCALAPPDATA，只设 APPDATA 是无效的，
# 那样冒烟脚本会把学期/课程/课堂写进用户真实数据目录。两个变量都要改。
TMP = tempfile.mkdtemp(prefix="ca_smoke_")
os.environ["LOCALAPPDATA"] = TMP
os.environ["APPDATA"] = TMP

from backend import config, db, schedule, dateparse, important, schemas
from backend.ai import manifest
from backend.api import make_server
from datetime import date, timedelta

cfg = config.init_config()
# 守卫：确认确实落在临时目录，防止以后改坏又写回用户目录
assert os.path.abspath(TMP) in os.path.abspath(cfg.dirs.data_dir), (
    f"冒烟脚本未使用临时数据目录，会污染用户数据：{cfg.dirs.data_dir}")
db.init_db()
print("DB OK, path =", cfg.dirs.db_path())

# 学期 + 课程 + 课表
sem_id = db.create_semester("2026秋季学期", "2026-09-07", "2027-01-15")
db.set_active_semester(sem_id)
cid = db.create_course(sem_id, "消费者行为学", code="MKT301", teacher_name="李老师",
                       location="H201", language_mode="zh", color="#4CAF50")
db.replace_slots(cid, [
    {"weekday": 0, "start_time": "10:00", "end_time": "11:40", "location": "H201"},  # 周一
    {"weekday": 2, "start_time": "14:00", "end_time": "15:40", "location": "H201"},  # 周三
])
print("semester/course/slots OK")

# 本周计划：取学期第一周的周一
center = date(2026, 9, 7)
plan = schedule.build_week_plan(db.get_active_semester(), db.list_courses(semester_id=sem_id), center)
mon = [d for d in plan if d["weekday"] == 0][0]
assert any(s["course_id"] == cid for s in mon["slots"]), "周一应有消费者行为学"
print("week plan OK, 周一课程数 =", len(mon["slots"]))

# 日期解析
base = date(2026, 9, 14)
assert dateparse.parse_relative_date("十月二十日", base) == "2026-10-20", dateparse.parse_relative_date("十月二十日", base)
assert dateparse.parse_relative_date("下周五", base) is not None
print("dateparse OK:", dateparse.parse_relative_date("十月二十日", base), dateparse.parse_relative_date("下周五", base))

# schema 校验
ov_raw = '```json\n{"topics":[{"title":"消费者购买决策","summary":"介绍了决策过程。","start_seconds":320.5,"end_seconds":1150.2,"evidence_segment_ids":["21"]}]}\n```'
ov, err = schemas.normalize_overview(ov_raw)
assert ov and ov["topics"], err
print("overview normalize OK:", ov["topics"][0]["title"])

items_raw = '{"items":[{"category":"assignment","title":"第二次作业取消","details":"老师明确说不用交。","raw_date_text":"下周五","normalized_date":null,"action_required":false,"confidence":"explicit","evidence":[{"segment_id":"88","start_seconds":2605.2,"end_seconds":2638.4,"quote":"第二次作业大家不用交了"}]}]}'
its, err = schemas.normalize_items(items_raw, "2026-09-14")
assert its, err
print("items normalize OK:", its[0]["category"], its[0]["title"])

# merge
merged = important.merge_items(its + its)
assert len(merged) == 1, len(merged)
print("merge OK")

# FTS
sid = db.get_or_create_session(cid, "2026-09-14", start_time="10:00", end_time="11:40", week_number=1)
db.replace_segments(sid, [{"start_seconds": 320.5, "end_seconds": 1150.2, "text": "今天我们主要介绍消费者购买决策", "language": "zh", "is_uncertain": False}])
db.index_session(sid)
res = db.fts_search("消费者购买决策")
assert res, "FTS 应命中"
print("FTS OK, hits =", len(res))

# 重要事项保存 + 列表
db.replace_items(sid, [{"session_id": sid, "course_id": cid, **its[0]}])
db.update_session(sid, status="completed")
items_list = db.list_items({"course_id": cid})
assert items_list, "应列出重要事项"
print("items list OK, count =", len(items_list))

# HTTP API
server = make_server("127.0.0.1", 0)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(0.3)
with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state") as r:
    state = r.read().decode()
print("API /api/state OK, has_semester =", '"has_semester"' in state)
with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/semesters") as r:
    print("API /api/semesters OK")

# 清理临时数据目录（脚本自始至终都写在 TMP 里）
import shutil

db.close_all_connections()
shutil.rmtree(TMP, ignore_errors=True)

print("\n=== 后端冒烟测试全部通过（数据目录为临时目录，已清理） ===")
