from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    try:
        from gui_new import main as run_gui
    except ImportError as exc:
        raise RuntimeError(
            "无法载入 PyQt5 GUI。请确认已用新版 gui.py 替换旧版，"
            "并已安装 PyQt5 及项目依赖文件。"
        ) from exc
    return int(run_gui())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())