"""启动器：把 src 加入路径后运行 main。

用法：
  python run.py            # 打开桌面窗口
  python run.py --no-gui   # 仅启动本地后端（用于测试）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from main import main  # noqa: E402

if __name__ == "__main__":
    main()
