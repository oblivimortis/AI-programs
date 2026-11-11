"""打包入口：``python agent.py`` 与 PyInstaller 生成的 exe 都从这里启动。"""

from tui.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
