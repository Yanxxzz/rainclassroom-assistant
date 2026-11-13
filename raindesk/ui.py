import io
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk
import requests

from .client import RainClient, RainError
from .desk import Desk
from .live import listen
from .local import load_private, save_private
from .question import TYPES
from .providers import PROVIDERS
from .solver import SolverError, slide_image, solve


PAPER = "#ffffff"
INK = "#192a35"
MUTED = "#546975"
TEAL = "#006c70"
PALE = "#eef4f4"
FONT = "Microsoft YaHei UI"
LOCKED = {"已提交", "已自行作答", "提交中", "提交待核对"}


def readable_error(exc):
    if isinstance(exc, (RainError, SolverError, ValueError)):
        return str(exc)
    if isinstance(exc, requests.RequestException):
        return "网络请求未完成，请检查连接后重试。"
    return "操作未完成（%s）。" % type(exc).__name__


def background(func):
    threading.Thread(target=func, daemon=True).start()


class App:
    def __init__(self, root, *, initial_client=None, demo=False, lesson_id=""):
        self.root = root
        self.demo = demo
        self.start_lesson = lesson_id
        self.client = initial_client or RainClient()
        self.config = load_private("settings.dat") or {}
        self.config.setdefault("provider", "GPT")
        self.bus = queue.Queue()
        self.generation = 0
        self.stop = threading.Event()
        self.desk = None
        self.entries = []
        self.current = None
        self.active_dialog = None
        self.session_busy = False
        self.closing = False
        root.title("雨课堂答题助手" + (" · 演练课堂" if demo else ""))
        root.geometry("1020x740")
        root.minsize(850, 630)
        root.option_add("*Font", (FONT, 10))
        root.configure(bg=PAPER)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=(FONT, 10), background=PAPER, foreground=INK)
        style.configure("TButton", padding=(10, 6), background=PALE, borderwidth=0)
        style.map("TButton", background=[("active", "#dae6e6")])
        style.configure("Send.TButton", background=TEAL, foreground="white", padding=(16, 8))
        style.map("Send.TButton", background=[("disabled", "#94a4a6"), ("active", "#005658")])
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=(FONT, 16, "bold"))
        style.configure("Treeview", rowheight=35, background=PAPER, fieldbackground=PAPER, font=(FONT, 10))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        head = ttk.Frame(root, padding=(20, 12))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="雨课堂答题助手", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.account = tk.StringVar(value="标准版雨课堂")
        ttk.Label(head, textvariable=self.account, style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=12)
        ttk.Button(head, text="设置", command=self.settings).grid(row=0, column=2)
        self.auth_btn = ttk.Button(head, text="扫码登录", command=self.login)
        self.auth_btn.grid(row=0, column=3, padx=(8, 0))
        bar = ttk.Frame(root, padding=(20, 0, 20, 12))
        bar.grid(row=1, column=0, sticky="ew")
        ttk.Label(bar, text="当前课堂").pack(side="left")
        self.course = ttk.Combobox(bar, state="readonly", width=32)
        self.course.pack(side="left", padx=8)
        self.refresh_btn = ttk.Button(bar, text="刷新", command=self.refresh)
        self.refresh_btn.pack(side="left")
        self.join_btn = ttk.Button(bar, text="进入并监听", command=self.join)
        self.join_btn.pack(side="left", padx=8)
        self.lessons = []

        main = ttk.Panedwindow(root, orient="horizontal")
        main.grid(row=2, column=0, sticky="nsew", padx=20)
        left = ttk.Frame(main, width=245, padding=(0, 5, 12, 0))
        right = ttk.Frame(main, padding=(14, 3, 0, 0))
        main.add(left, weight=1)
        main.add(right, weight=4)
        ttk.Label(left, text="课堂题目", font=(FONT, 12, "bold")).pack(anchor="w", pady=(0, 7))
        self.list = ttk.Treeview(left, show="tree", selectmode="browse", height=10)
        self.list.column("#0", width=210, minwidth=125)
        self.list.pack(fill="both", expand=True)
        self.list.bind("<<TreeviewSelect>>", self.select)
        self.title = tk.StringVar(value="等待老师发题")
        self.timer = tk.StringVar()
        header = ttk.Frame(right)
        header.pack(fill="x", pady=(0, 6))
        ttk.Label(header, textvariable=self.title, font=(FONT, 13, "bold")).pack(side="left")
        ttk.Label(header, textvariable=self.timer, style="Muted.TLabel").pack(side="right")
        self.question = self.textbox(right, 5, readonly=True, expand=True)
        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=6)
        self.pic_btn = ttk.Button(actions, text="查看题面图", command=self.show_picture)
        self.pic_btn.pack(side="left")
        self.solve_btn = ttk.Button(actions, text="重新解题", command=self.retry)
        self.solve_btn.pack(side="left", padx=6)
        self.done_btn = ttk.Button(actions, text="已在微信作答", command=self.mark_done)
        self.done_btn.pack(side="right")
        self.answer_label = tk.StringVar(value="答案")
        ttk.Label(right, textvariable=self.answer_label, font=(FONT, 11, "bold")).pack(anchor="w", pady=(5, 3))
        self.editor = self.textbox(right, 4, expand=True)
        self.editor.bind("<<Modified>>", self.edit)
        ttk.Label(right, text="解析", font=(FONT, 11, "bold")).pack(anchor="w", pady=(8, 3))
        self.explanation = self.textbox(right, 3, readonly=True)
        end = ttk.Frame(right)
        end.pack(fill="x", pady=(10, 3))
        self.review_state = tk.StringVar()
        ttk.Label(end, textvariable=self.review_state, style="Muted.TLabel").pack(side="left")
        self.send_btn = ttk.Button(end, text="核对并提交…", style="Send.TButton", command=self.review)
        self.send_btn.pack(side="right")
        self.status = tk.StringVar(value="正在读取登录状态…")
        ttk.Label(root, textvariable=self.status, style="Muted.TLabel", padding=(20, 10)).grid(row=3, column=0, sticky="ew")
        self.controls()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(60, self.pump)
        root.after(250, self.tick)
        if not demo:
            root.after(80, self.restore)

    def textbox(self, parent, height, readonly=False, expand=False):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=expand)
        text = tk.Text(frame, height=height, wrap="word", font=(FONT, 11), foreground=INK,
                       bg="#f5f8f8" if readonly else PAPER, relief="solid", bd=1,
                       highlightthickness=0, padx=8, pady=6, undo=not readonly)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        if readonly:
            text.configure(state="disabled")
        return text

    @staticmethod
    def fill(widget, value, readonly=True):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.edit_modified(False)
        if readonly:
            widget.configure(state="disabled")

    def task(self, fn, success, failure=None):
        generation = self.generation

        def run():
            try:
                result = fn()
                self.bus.put((generation, success, result))
            except Exception as exc:
                self.bus.put((generation, failure or self.failed, readable_error(exc)))
        background(run)

    def pump(self):
        try:
            while True:
                gen, callback, value = self.bus.get_nowait()
                if gen == self.generation:
                    callback(value)
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(75, self.pump)

    def failed(self, msg):
        self.session_busy = False
        self.status.set(msg)
        self.controls()

    def controls(self):
        signed = bool(self.client.uid)
        can_switch = not self.session_busy and not any(e.state == "提交中" for e in self.entries)
        self.auth_btn.configure(state="normal" if can_switch and not self.demo else "disabled")
        self.refresh_btn.configure(state="normal" if signed and can_switch and not self.demo else "disabled")
        self.join_btn.configure(state="normal" if self.lessons and can_switch and not self.demo else "disabled")
        entry = self.current
        writable = bool(entry and self.desk and self.desk.active and not entry.question.expired and entry.state not in LOCKED)
        self.solve_btn.configure(state="normal" if writable and entry.state != "解题中" else "disabled")
        self.done_btn.configure(state="normal" if entry and entry.state not in ("提交中", "已提交", "已自行作答") else "disabled")
        self.pic_btn.configure(state="normal" if entry and entry.picture else "disabled")
        self.send_btn.configure(state="normal" if (writable and self.desk.online and entry.text.strip())
                                or (entry and entry.state in ("提交待核对", "已提交")) else "disabled")
        self.send_btn.configure(text={"提交待核对": "核对提交状态…", "已提交": "查看提交回执"}.get(
            entry.state if entry else "", "核对并提交…"))
        self.editor.configure(state="normal" if writable else "disabled")

    def restore(self):
        self.session_busy = True
        self.controls()
        self.task(self.client.restore, lambda ok: self.signed_in() if ok else self.failed("请扫码登录标准版雨课堂。"))

    def login(self):
        if self.client.uid:
            if not messagebox.askyesno("退出账号", "退出当前账号并清除本机登录状态？", parent=self.root):
                return
            self.reset_class()
            self.client.forget()
            self.client = RainClient()
            self.lessons.clear()
            self.course.set("")
            self.course.configure(values=[])
            self.auth_btn.configure(text="扫码登录")
            self.account.set("标准版雨课堂")
            self.failed("已退出账号。")
            return
        self.reset_class()
        client = self.client = RainClient()
        self.session_busy = True
        self.controls()
        win = tk.Toplevel(self.root)
        win.title("微信扫码登录")
        win.transient(self.root)
        wrap = ttk.Frame(win, padding=18)
        wrap.pack()
        label = ttk.Label(wrap, text="正在获取登录二维码…")
        label.pack()
        poster = ttk.Label(wrap)
        poster.pack(pady=8)
        stop, gen = self.stop, self.generation

        def cancel():
            self.reset_class()
            self.client = RainClient()
            win.destroy()
            self.failed("已取消扫码。")

        def show(data):
            img = Image.open(io.BytesIO(data)).convert("RGB").resize((265, 265), Image.Resampling.NEAREST)
            poster.picture = ImageTk.PhotoImage(img)
            poster.configure(image=poster.picture)
            label.configure(text="用手机微信扫一扫，并确认登录")

        def authenticate():
            while not stop.is_set():
                token, image = client.new_qr()
                self.bus.put((gen, show, image))
                if client.poll_qr(token, cancelled=stop):
                    return

        def finish(_):
            win.destroy()
            self.signed_in()

        def error(msg):
            win.destroy()
            self.failed(msg)

        win.protocol("WM_DELETE_WINDOW", cancel)
        self.task(authenticate, finish, error)

    def signed_in(self):
        self.session_busy = False
        self.account.set(self.client.name)
        self.auth_btn.configure(text="退出账号")
        self.refresh()

    def refresh(self):
        self.session_busy = True
        self.controls()
        self.status.set("正在查找进行中的课堂…")
        self.task(self.client.lessons, self.show_lessons)

    def show_lessons(self, lessons):
        self.session_busy = False
        self.lessons = lessons
        self.course.configure(values=[x.name for x in lessons])
        if lessons:
            self.course.current(0)
        else:
            self.course.set("")
        self.status.set("选择课堂后点击「进入并监听」。" if lessons else "当前没有进行中的课堂。开课后可刷新。")
        self.controls()
        if self.start_lesson:
            matches = [i for i, lesson in enumerate(lessons) if lesson.id == self.start_lesson]
            self.start_lesson = ""
            if matches:
                self.course.current(matches[0])
                self.join()

    def reset_class(self):
        self.generation += 1
        self.stop.set()
        self.stop = threading.Event()
        if self.desk:
            self.desk.active = self.desk.online = False
        self.desk = None
        self.entries.clear()
        self.list.delete(*self.list.get_children())
        self.current = None
        self.render()

    def join(self):
        i = self.course.current()
        if not 0 <= i < len(self.lessons):
            return
        lesson = self.lessons[i]
        self.reset_class()
        self.session_busy = True
        self.controls()
        # The outgoing socket keeps its own client while its worker exits.
        prior = self.client
        client = RainClient()
        client.http.cookies.update(prior.http.cookies)
        client.uid, client.name = prior.uid, prior.name
        self.client = client
        self.status.set("正在进入 " + lesson.name)
        self.task(lambda: client.enter(lesson), lambda _: self.monitor())

    def monitor(self):
        self.session_busy = False
        self.desk = Desk(self.client, self.config, remember=not self.demo)
        self.status.set("正在连接 " + self.client.lesson.name)
        self.controls()
        gen, stop, client = self.generation, self.stop, self.client

        def emit(kind, payload):
            if kind != "event_kind":
                self.bus.put((gen, lambda value: self.receive(kind, value), payload))

        if not self.demo:
            self.task(lambda: listen(client, stop, emit), lambda _: None, lambda msg: self.receive("offline", msg))

    def receive(self, kind, payload):
        if kind == "question":
            entry = self.desk.add(payload)
            if entry is None:
                return
            self.entries.append(entry)
            row = str(len(self.entries) - 1)
            self.list.insert("", "end", iid=row, text=self.row_label(entry))
            if not self.current or self.current.state in LOCKED or self.current.question.expired:
                self.list.selection_set(row)
                self.select()
            self.list.see(row)
            if entry.state not in LOCKED:
                self.start_solve(entry)
        elif kind == "changed":
            entry = self.desk.entries.get(payload.key)
            if entry:
                entry.question = payload
        elif kind == "connected":
            self.desk.online = True
            self.status.set(self.client.lesson.name + " · 已连接，自动监听老师发题")
        elif kind in ("ended", "offline"):
            self.desk.online = False
            if kind == "ended":
                self.desk.active = False
            self.status.set(payload)
        elif kind == "status":
            self.status.set(payload)
        self.controls()

    def row_label(self, e):
        n = self.entries.index(e) + 1
        state = "已截止" if e.question.expired and e.state not in LOCKED else e.state
        return "%02d   %s    %s" % (n, TYPES.get(e.question.kind, "题目"), state)

    def select(self, _event=None):
        selected = self.list.selection()
        self.current = self.entries[int(selected[0])] if selected else None
        self.render()

    def render(self):
        e = self.current
        self.title.set(TYPES.get(e.question.kind, "题目") + " · 第 %d 题" % (self.entries.index(e) + 1)
                       if e else "等待老师发题")
        self.fill(self.question, e.question.display_text() if e else "")
        hint = {4: "答案 · 每空一行", 5: "答案正文 · 提交时自动加署名"}.get(e.question.kind, "答案 · 填写选项编号") if e else "答案"
        self.answer_label.set(hint)
        self.fill(self.editor, e.text if e else "", readonly=not e or e.state in LOCKED)
        self.fill(self.explanation, e.explanation if e else "")
        self.review_state.set(e.state if e else "")
        self.update_timer()
        self.controls()

    def update_row(self, entry, redraw=False):
        row = str(self.entries.index(entry))
        self.list.item(row, text=self.row_label(entry))
        if entry is self.current:
            if redraw:
                self.render()
            else:
                self.review_state.set(entry.state)
        self.controls()

    def edit(self, _event=None):
        if not self.editor.edit_modified():
            return
        self.editor.edit_modified(False)
        e = self.current
        if e and e.state not in LOCKED:
            e.text = self.editor.get("1.0", "end-1c")
            e.revision += 1
            e.state = "待确认"
            self.update_row(e)

    def start_solve(self, entry):
        q = entry.question
        if q.expired or entry.state in LOCKED:
            return
        entry.state = "解题中"
        entry.revision += 1
        revision = entry.revision
        config = dict(self.config)
        old_pic = entry.picture
        self.update_row(entry)

        def generate():
            picture, image_error = old_pic, ""
            if q.image_url and not picture:
                try:
                    picture = slide_image(q.image_url)
                except (SolverError, OSError, requests.RequestException, ValueError):
                    image_error = "当前题面图未能读取。"
            if q.expired:
                raise ValueError("作答时间已结束。")
            answer, why = solve(q, config, picture, image_error)
            return picture, answer, why

        def finish(result):
            pic, answer, why = result
            entry.picture = pic
            if entry.revision != revision:
                return
            entry.text, entry.explanation, entry.state = answer, why, "待确认"
            self.update_row(entry, redraw=True)

        def error(msg):
            if entry.revision == revision:
                entry.state, entry.explanation = "需核对", msg
                self.update_row(entry, redraw=True)

        self.task(generate, finish, error)

    def retry(self):
        if self.current:
            self.start_solve(self.current)

    def update_timer(self):
        e = self.current
        if e and e.question.deadline is not None:
            remaining = max(0, math.ceil(e.question.deadline - time.time()))
            self.timer.set("剩余 %d:%02d" % divmod(remaining, 60) if remaining else "已截止")
        else:
            self.timer.set("")

    def tick(self):
        self.update_timer()
        self.controls()
        if not self.closing:
            self.root.after(600, self.tick)

    def show_picture(self):
        if not self.current or not self.current.picture:
            return
        win = tk.Toplevel(self.root)
        win.title("本题课件")
        img = Image.open(io.BytesIO(self.current.picture))
        img.thumbnail((int(win.winfo_screenwidth() * .8), int(win.winfo_screenheight() * .76)))
        win.photo = ImageTk.PhotoImage(img)
        ttk.Label(win, image=win.photo).pack()

    def mark_done(self):
        if self.current:
            self.desk.completed_elsewhere(self.current)
            self.update_row(self.current, redraw=True)

    def review(self):
        entry, desk = self.current, self.desk
        if not entry or not desk:
            return
        if entry.state == "已提交":
            r = entry.receipt or {}
            if "code" in r:
                stamp = time.strftime("%H:%M:%S", time.localtime(r["time"]))
                result = r["result"]
                text = "\n".join(result) if isinstance(result, list) else result
                info = "%s    HTTP %d    雨课堂返回码 %s\n%s\n\n%s" % (
                    stamp, r["http"], r["code"], r.get("message") or "服务端已接收", text)
            else:
                info = "早期版本仅保留了提交状态。完整作答可在微信雨课堂中查看。"
            messagebox.showinfo("提交回执", info, parent=self.root)
            return
        if entry.state == "提交待核对":
            if messagebox.askyesno("核对回执", "请先查看微信里的作答记录。已确认这道题没有提交，要重新编辑吗？", parent=self.root):
                desk.record(entry, "待确认")
                entry.state = "待确认"
                self.update_row(entry, redraw=True)
            return
        try:
            result = desk.preview(entry)
        except ValueError as exc:
            messagebox.showinfo("核对答案", str(exc), parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("确认这道题的答案")
        win.transient(self.root)
        win.grab_set()
        self.active_dialog = win
        block = ttk.Frame(win, padding=18)
        block.pack(fill="both", expand=True)
        ttk.Label(block, text=self.client.lesson.name + " · " + self.title.get(), font=(FONT, 12, "bold")).pack(anchor="w")
        ttk.Label(block, text=entry.question.display_text()[:220], wraplength=500).pack(anchor="w", pady=8)
        ttk.Label(block, text="将提交以下内容", style="Muted.TLabel").pack(anchor="w")
        box = self.textbox(block, 8, readonly=True, expand=True)
        out = "\n".join(result) if isinstance(result, list) else result
        self.fill(box, out)
        buttons = ttk.Frame(block)
        buttons.pack(fill="x", pady=(12, 0))

        def dismiss():
            self.active_dialog = None
            win.destroy()

        def send():
            dismiss()
            self.task(lambda: desk.confirm(entry, result),
                      lambda _: self.sent(entry), lambda msg: self.submit_error(entry, msg))
            self.send_btn.configure(state="disabled")

        ttk.Button(buttons, text="返回编辑", command=dismiss).pack(side="left")
        confirm = ttk.Button(buttons, text="确认提交", style="Send.TButton", command=send)
        confirm.pack(side="right")
        win.protocol("WM_DELETE_WINDOW", dismiss)

    def sent(self, entry):
        self.status.set("演练提交成功。" if self.demo else "雨课堂已接收你的答案。")
        self.update_row(entry, redraw=True)

    def submit_error(self, entry, msg):
        self.status.set(msg)
        self.update_row(entry, redraw=True)
        messagebox.showinfo("提交状态", msg, parent=self.root)

    def settings(self):
        win = tk.Toplevel(self.root)
        win.title("答题设置")
        win.transient(self.root)
        win.grab_set()
        block = ttk.Frame(win, padding=18)
        block.pack(fill="both", expand=True)
        ttk.Label(block, text="主观题署名（姓名和学号）").pack(anchor="w")
        signature = ttk.Entry(block, width=38)
        signature.insert(0, self.config.get("signature", ""))
        signature.pack(fill="x", pady=(4, 14))
        ttk.Label(block, text="解题服务").pack(anchor="w")
        provider = ttk.Combobox(block, state="readonly", values=PROVIDERS)
        provider.set(self.config.get("provider", "GPT"))
        provider.pack(fill="x", pady=(4, 12))
        ttk.Label(block, text="GPT 模型（留空使用 Codex 默认模型）").pack(anchor="w")
        model = ttk.Entry(block, width=38)
        model.insert(0, self.config.get("model", ""))
        model.pack(fill="x", pady=(4, 14))
        paths = dict(self.config.get("paths", {}))
        helper = ttk.Frame(block)
        helper.pack(fill="x", pady=(0, 10))
        label = ttk.Label(helper, text="服务程序或 CLI")
        path_value = tk.StringVar()
        field = ttk.Entry(helper, textvariable=path_value, width=38)
        def browse():
            path = filedialog.askopenfilename(parent=win, title="选择 " + provider.get() + " 程序",
                                              filetypes=[("程序或 CLI", "*.exe *.cmd *.bat *.mjs *.cjs *.js"), ("所有文件", "*.*")])
            if path:
                path_value.set(path)
        choose = ttk.Button(helper, text="浏览…", command=browse)
        selected = [None]
        def switch(_event=None):
            if selected[0] and selected[0] != "GPT":
                paths[selected[0]] = path_value.get().strip()
            selected[0] = provider.get()
            if provider.get() == "GPT":
                for widget in (label, field, choose):
                    widget.pack_forget()
                model.configure(state="normal")
            else:
                model.configure(state="disabled")
                label.pack(anchor="w")
                field.pack(side="left", expand=True, fill="x")
                choose.pack(side="right", padx=4)
                path_value.set(paths.get(provider.get(), ""))
        provider.bind("<<ComboboxSelected>>", switch)
        switch()

        def store():
            if provider.get() != "GPT":
                paths[provider.get()] = path_value.get().strip()
            paths.pop("GPT", None)
            self.config.update(signature=signature.get().strip(), model=model.get().strip(),
                               provider=provider.get(), paths=paths)
            save_private("settings.dat", self.config)
            win.destroy()
            self.status.set("设置已保存。")
        ttk.Button(block, text="保存设置", style="Send.TButton", command=store).pack(anchor="e")

    def close(self):
        self.stop.set()
        if self.desk:
            self.desk.active = False
        self.generation += 1
        self.closing = True
        self.root.destroy()
