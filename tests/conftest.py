"""pytest 公共夹具：把应用数据目录指向临时目录，保证测试互不污染。

必须在导入任何 backend 模块之前设置环境变量。

坑（真实踩过）：paths._appdata() 在 Windows 上**优先读 LOCALAPPDATA**，
只设 APPDATA 是无效的 —— 结果所有测试都会读写（甚至删掉）用户的真实数据目录
%LOCALAPPDATA%\\ClassroomAssistant。这里两个变量一起指向临时目录。
"""
import os
import sys
import tempfile

# 提前把 src 加入路径，并把 APPDATA/LOCALAPPDATA 指向临时目录（backend 在首次 get_config 时读取）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_TMP = tempfile.mkdtemp(prefix="ca_test_")
os.environ["LOCALAPPDATA"] = _TMP
os.environ["APPDATA"] = _TMP

import pytest  # noqa: E402

from backend import config, db, paths  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_app(tmp_path):
    """每个测试使用独立数据目录与全新数据库。"""
    data_dir = str(tmp_path / "appdata")
    os.environ["LOCALAPPDATA"] = data_dir
    os.environ["APPDATA"] = data_dir
    db.close_all_connections()
    cfg = config.init_config()
    # 清空可能存在的旧库文件
    for f in ("classroom-assistant.db", "classroom-assistant.db-wal", "classroom-assistant.db-shm"):
        p = os.path.join(cfg.dirs.database, f)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    db.init_db()
    yield
    db.close_all_connections()


def test_test_suite_does_not_touch_real_user_data():
    """守卫：测试绝不能落到用户真实数据目录。

    这条断言放在 conftest 里，任何一次测试运行都会先验证隔离是否生效。
    """
    real = os.path.join(
        os.path.expanduser("~"), "AppData", "Local", "ClassroomAssistant")
    cur = os.path.abspath(config.get_config().dirs.data_dir)
    assert os.path.abspath(real) not in cur, (
        f"测试数据目录仍是用户真实目录，会破坏用户数据：{cur}")
    assert cur != os.path.abspath(real)


@pytest.fixture
def helpers():
    return {"config": config, "db": db, "paths": paths}
