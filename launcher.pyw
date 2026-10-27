"""桌面启动器：不弹黑窗口，出错时弹消息框。

由桌面快捷方式调用。pythonw 没有控制台，所以任何异常都必须自己弹出来，
否则用户会看到"点了没反应"。
"""

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)


def _fail(message: str, detail: str = "") -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("视频字幕提取 启动失败", message + (f"\n\n{detail}" if detail else ""))
        root.destroy()
    except Exception:
        pass
    sys.exit(1)


def main() -> None:
    venv_python = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(venv_python):
        _fail("找不到运行环境（.venv）。", "请确认项目目录完整，或重新安装依赖。")
    if os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        # 被系统 Python 调起来了，转到虚拟环境
        os.execv(venv_python, [venv_python, os.path.abspath(__file__)])

    try:
        from subtitler.gui import App

        App().mainloop()
    except Exception:
        _fail("程序启动时出错。", traceback.format_exc(limit=3))


if __name__ == "__main__":
    main()
