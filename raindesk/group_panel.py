"""Window picker and classroom follow-up for codes seen in that window."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from .group_watch import GroupWatch
from .wechat_window import chat_windows


class GroupPanel:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.window = None
        self.code_list = None
        self.watch = None
        self.events = queue.Queue()
        self.codes = {}
        self.current = None
        self.course_id = ""
        self.automatic = False
        self.request = 0
        self.timer = None
        self.deadline = 0
        self.entering = None
        self.previous_lesson = ""
        self.terminal_error = False
        self.source = tk.StringVar(master=self.root)
        self.status = tk.StringVar(master=self.root)

    def show(self):
        if self.window:
            self.window.lift()
            return
        win = self.window = tk.Toplevel(self.root)
        win.title("监听微信群窗口")
        win.transient(self.root)
        box = ttk.Frame(win, padding=16)
        box.pack(fill="both", expand=True)
        box.columnconfigure(0, weight=1)
        ttk.Label(box, text="在微信聊天列表中双击目标群，打开独立窗口，再刷新选择。").grid(
            row=0, column=0, columnspan=2, sticky="w")
        picker = ttk.Combobox(box, state="readonly", width=43)
        picker.grid(row=1, column=0, sticky="ew", pady=6)
        choices = []

        def refresh():
            choices[:] = chat_windows()
            picker.configure(values=[f"{w.title}  ·  {w.hwnd:X}" for w in choices])
            if choices:
                picker.current(choices.index(self.watch.target) if self.watch and self.watch.target in choices else 0)
            else:
                picker.set("")
                if not self.watch:
                    self.status.set("尚未找到独立的微信群窗口。")

        ttk.Button(box, text="刷新窗口", command=refresh).grid(row=1, column=1, padx=(6, 0))
        ttk.Label(box, text="自动跟进的课程").grid(row=2, column=0, sticky="w")
        courses = {"": "仅识码，由我选择课堂"}
        courses.update({v.course_id: v.name for v in self.app.lessons if v.course_id})
        course = ttk.Combobox(box, state="readonly", width=43)
        course.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 6))

        def load_courses(values):
            if self.window is not win:
                return
            selected = list(courses)[course.current()] if course.current() >= 0 else self.course_id
            if self.course_id in values and self.course_id not in courses and not selected:
                selected = self.course_id
            courses.update(values)
            course.configure(values=[name if not key else f"{name}  ·  {key}" for key, name in courses.items()])
            course.current(list(courses).index(selected) if selected in courses else 0)

        load_courses({})
        if self.app.client.uid:
            self.app.task(self.app.client.course_names, load_courses, lambda _: None)
        automatic = tk.BooleanVar(master=win, value=self.automatic)
        ttk.Checkbutton(box, text="发现新码时调用微信识别，并跟进所选课程",
                        variable=automatic).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Label(box, textvariable=self.source, wraplength=460).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(box, textvariable=self.status, wraplength=460).grid(row=6, column=0, columnspan=2, sticky="w")
        self.code_list = ttk.Combobox(box, state="readonly", width=43)
        self.code_list.grid(row=7, column=0, sticky="ew", pady=6)
        ttk.Button(box, text="处理此码", command=self.handle_selected).grid(row=7, column=1, padx=(6, 0))
        self.update_codes()
        controls = ttk.Frame(box)
        controls.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(5, 0))

        def start():
            index = picker.current()
            if not 0 <= index < len(choices):
                self.status.set("请先选择独立群窗口。")
                return
            self.start(choices[index], list(courses)[course.current()], automatic.get())

        ttk.Button(controls, text="开始监听", command=start).pack(side="left")
        ttk.Button(controls, text="停止监听", command=self.stop).pack(side="left", padx=5)

        def hide():
            self.window = None
            self.code_list = None
            win.destroy()

        ttk.Button(controls, text="收起", command=hide).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", hide)
        refresh()

    def start(self, target, course_id, automatic):
        self.stop()
        if not target.available():
            self.status.set("请还原所选群窗口。")
            return
        self.course_id, self.automatic = course_id, automatic
        self.terminal_error = False
        watcher = GroupWatch(target, lambda kind, value: self.events.put((watcher, kind, value)))
        self.watch = watcher
        self.app.group_btn.configure(text="群窗口监听中…")
        self.source.set("所选窗口：" + target.title)
        self.status.set("等待新二维码…")
        threading.Thread(target=watcher.run, daemon=True).start()

    def cancel_pending(self):
        self.request += 1
        if self.watch:
            self.watch.cancel_scan()
        self.current = self.entering = None
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None

    def stop(self):
        self.cancel_pending()
        if self.watch:
            self.watch.stop.set()
        self.watch = None
        self.codes.clear()
        self.source.set("")
        self.status.set("群窗口监听已停止。")
        self.app.group_btn.configure(text="监听微信群窗口")
        if self.code_list is not None:
            self.update_codes()

    def update_codes(self):
        if self.code_list is None:
            return
        choices = [("微信场景码" if code.kind == "wechat" else "雨课堂课堂码") + f" {i}"
                   for i, code in enumerate(self.codes.values(), 1)]
        self.code_list.configure(values=choices)
        if choices:
            self.code_list.current(len(choices) - 1)
        else:
            self.code_list.set("")

    def drain(self):
        while True:
            try:
                watcher, kind, value = self.events.get_nowait()
            except queue.Empty:
                return
            if watcher is not self.watch:
                continue
            if kind == "status":
                self.source.set(watcher.target.title + " · " + value)
                if "暂停" in value:
                    self.app.group_btn.configure(text="群窗口监听已暂停…")
                elif self.codes:
                    self.app.group_btn.configure(text="群窗口已识码…")
                else:
                    self.app.group_btn.configure(text="群窗口监听中…")
            elif kind == "codes":
                initial, codes = value
                self.codes.update((code.key, code) for code in codes)
                self.update_codes()
                self.app.group_btn.configure(text="群窗口已识码…")
                self.status.set("已识码。选中记录后可点「处理此码」。")
                if self.automatic and not initial and len(codes) == 1:
                    self.process(codes[0])
                elif initial or len(codes) > 1:
                    self.status.set("已识别开始时已有的码或同屏多个新码，请选择要处理的记录。")
            elif kind == "scan":
                key, invoked = value
                if self.current and self.current.key == key and self.entering is None:
                    if invoked is None:
                        self.status.set("已识码。无法核实微信菜单的执行结果，请查看微信提示。")
                    else:
                        self.status.set("已调用微信识码，请查看微信提示。"
                                        if invoked else "已识码。请在群中右键该图选择「识别图中二维码」，必要时点开原图。")
            elif kind == "lessons":
                request, values = value
                if values is None:
                    self.later(request)
                else:
                    self.match(request, values)
            elif kind == "error":
                self.terminal_error = True
                self.status.set(value)
            elif kind == "ended":
                self.cancel_pending()
                self.watch = None
                self.source.set("")
                self.app.group_btn.configure(text="监听微信群窗口")
                if not self.terminal_error:
                    self.status.set("所选窗口已关闭或更换，监听已结束。")

    def handle_selected(self):
        i = self.code_list.current()
        if self.watch and 0 <= i < len(self.codes):
            self.process(list(self.codes.values())[i])

    def process(self, code):
        self.cancel_pending()
        self.current = code
        self.previous_lesson = self.app.client.lesson.id if self.app.client.lesson else ""
        if code.kind == "wechat":
            self.watch.request_scan(code)
            self.status.set("已发现微信场景码，正在调用微信识别…")
        if not self.app.client.uid or (not self.course_id and not code.lesson_id):
            if code.kind == "rain":
                self.status.set("已识别雨课堂码。请登录雨课堂后处理。")
            return
        self.deadline = time.monotonic() + 90
        self.poll(self.request)

    def poll(self, request):
        self.timer = None
        if request != self.request or not self.watch:
            return
        if self.app.session_busy or any(e.state == "提交中" for e in self.app.entries):
            self.later(request)
            return
        # Use a separate read-only session. A classroom switch replaces the
        # listener, but its generation must not discard this watch's completion.
        from .client import RainClient
        reader = RainClient()
        reader.http.cookies.update(self.app.client.http.cookies)
        watcher = self.watch

        def read():
            try:
                values = reader.lessons()
            except Exception:
                values = None
            finally:
                reader.http.close()
            self.events.put((watcher, "lessons", (request, values)))

        threading.Thread(target=read, daemon=True).start()

    def later(self, request):
        if request != self.request or not self.watch:
            return
        if time.monotonic() >= self.deadline:
            self.status.set("已识码，当前账号尚未匹配到唯一的目标课堂。完成微信入课后可再次处理。")
        else:
            self.timer = self.root.after(3500, lambda: self.poll(request))

    def match(self, request, lessons):
        if request != self.request or not self.watch or not self.current:
            return
        code = self.current
        matches = [l for l in lessons if (not code.lesson_id or l.id == code.lesson_id)
                   and (not self.course_id or l.course_id == self.course_id)
                   and (code.lesson_id or l.id != self.previous_lesson)]
        if self.app.session_busy or any(e.state == "提交中" for e in self.app.entries):
            self.later(request)
        elif len(matches) == 1:
            lesson = matches[0]
            if self.app.desk and self.app.desk.active and self.app.client.lesson == lesson:
                if self.app.desk.online:
                    self.status.set("已在目标课堂 · " + lesson.name)
                else:
                    self.later(request)
                return
            self.entering = lesson.id
            self.status.set("已匹配目标课堂，正在进入 " + lesson.name)
            self.app.join_lesson(lesson, watch_request=request)
        else:
            self.later(request)

    def connected(self):
        if self.entering and self.app.client.lesson.id == self.entering:
            self.status.set("已进入课堂并开始收题 · " + self.app.client.lesson.name)
            self.entering = None
