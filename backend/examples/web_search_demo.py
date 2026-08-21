"""web_search 工具演示脚本（调用真实 API，需要 TAVILY_API_KEY）。

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe examples\\web_search_demo.py
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.tools.web_search import web_search


def main():
    print(web_search("Python 3.14 最新版本"))


if __name__ == "__main__":
    main()
