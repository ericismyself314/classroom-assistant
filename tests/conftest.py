"""pytest 公共夹具：把应用数据目录指向临时目录，保证测试互不污染。

必须在导入任何 backend 模块之前设置环境变量。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import pytest  # noqa: E402

from backend import config, db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_app(tmp_path):
    """每个测试使用独立数据目录与全新数据库。"""
    data_dir = str(tmp_path / "appdata")
    os.environ["LOCALAPPDATA"] = data_dir
    os.environ["APPDATA"] = data_dir
    db.close_all_connections()
    cfg = config.init_config()
    for name in ("classroom-assistant.db", "classroom-assistant.db-wal", "classroom-assistant.db-shm"):
        path = os.path.join(cfg.dirs.database, name)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    db.init_db()
    yield
    db.close_all_connections()


def test_test_suite_does_not_touch_real_user_data():
    """守卫：测试数据不能指向用户真实应用目录。"""
    real = os.path.abspath(os.path.join(os.path.expanduser("~"), "AppData", "Local", "ClassroomAssistant"))
    current = os.path.abspath(config.get_config().dirs.data_dir)
    assert real not in current, f"测试数据目录指向用户真实数据：{current}"


@pytest.fixture
def helpers():
    return {"config": config, "db": db}
