import ctypes
import argparse
import sys
import tkinter as tk

from raindesk.ui import App


def main():
    options = argparse.ArgumentParser(description="雨课堂 Windows 答题助手")
    options.add_argument("--lesson", metavar="ID", default="", help="恢复监听本人在课列表中的指定课堂")
    selected = options.parse_args()
    if sys.platform == "win32":
        ctypes.windll.user32.SetProcessDPIAware()
    root = tk.Tk()
    App(root, lesson_id=selected.lesson)
    root.mainloop()


if __name__ == "__main__":
    main()
