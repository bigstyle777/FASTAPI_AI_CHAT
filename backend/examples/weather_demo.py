"""weather 工具演示脚本（调用真实 API）。

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe examples\\weather_demo.py
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.tools.weather import weather


def main():
    print(weather("Beijing"))


if __name__ == "__main__":
    main()
