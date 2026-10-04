#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PetForge · 图形界面

把 forge.py / install.py 包一层：选图 → 填参数 → 点生成 → 点安装。
编码在后台线程跑，日志实时回显，界面不会卡死。

用法：
  python gui.py
"""

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
PY = sys.executable or "python"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.proc = None
        self.q = queue.Queue()
        self.busy = False
        self.last_prefix = None

        root.title("PetForge —— 立绘 / 绿幕视频 → 桌宠透明动画")
        root.geometry("720x560")
        root.minsize(640, 480)

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Big.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 8))
        style.configure("TLabel", font=("Microsoft YaHei UI", 9))
        style.configure("Hint.TLabel", foreground="#6B7CA8", font=("Microsoft YaHei UI", 8))

        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(root, padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(frm, text="角色图").grid(row=row, column=0, sticky="w", **pad)
        self.var_img = tk.StringVar()
        ttk.Entry(frm, textvariable=self.var_img).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="浏览…", command=self.pick_image).grid(row=row, column=2, **pad)
        row += 1
        ttk.Label(frm, text="透明底 PNG 最好；纯色背景图请勾选下面的抠图",
                  style="Hint.TLabel").grid(row=row, column=1, sticky="w", padx=12)
        row += 1

        ttk.Label(frm, text="背景色").grid(row=row, column=0, sticky="w", **pad)
        bgbar = ttk.Frame(frm)
        bgbar.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        self.var_chroma = tk.BooleanVar(value=True)
        self.var_key = tk.StringVar(value="00FF00")
        ttk.Checkbutton(bgbar, text="抠掉背景色 #", variable=self.var_chroma).pack(side="left")
        ttk.Entry(bgbar, textvariable=self.var_key, width=10).pack(side="left", padx=(2, 10))
        ttk.Label(bgbar, text="绿幕填 00FF00，白底填 FFFFFF", style="Hint.TLabel").pack(side="left")
        row += 1

        ttk.Label(frm, text="宠物名").grid(row=row, column=0, sticky="w", **pad)
        self.var_name = tk.StringVar(value="小鲸·成人版")
        ttk.Entry(frm, textvariable=self.var_name).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Label(frm, text="显示名").grid(row=row, column=2, sticky="w", **pad)
        row += 1

        ttk.Label(frm, text="包前缀").grid(row=row, column=0, sticky="w", **pad)
        self.var_prefix = tk.StringVar(value="mature")
        ttk.Entry(frm, textvariable=self.var_prefix).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Label(frm, text="决定目录名", style="Hint.TLabel").grid(row=row, column=2, sticky="w", **pad)
        row += 1

        ttk.Label(frm, text="角色高度").grid(row=row, column=0, sticky="w", **pad)
        hbar = ttk.Frame(frm)
        hbar.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        self.var_h = tk.IntVar(value=330)
        ttk.Scale(hbar, from_=150, to=350, variable=self.var_h, orient="horizontal",
                  command=lambda v: self.lbl_h.config(text=f"{int(float(v))} px")).pack(side="left", fill="x", expand=True)
        self.lbl_h = ttk.Label(hbar, text="330 px", width=8)
        self.lbl_h.pack(side="left", padx=(8, 0))
        row += 1

        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        self.btn_gen = ttk.Button(btns, text="① 生成宠物包", style="Big.TButton", command=self.generate)
        self.btn_gen.pack(side="left")
        self.btn_inst = ttk.Button(btns, text="② 安装到 dsh-pet", style="Big.TButton",
                                   command=self.install, state="disabled")
        self.btn_inst.pack(side="left", padx=(10, 0))
        self.btn_stop = ttk.Button(btns, text="停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="right")
        row += 1

        ttk.Label(frm, text="日志").grid(row=row, column=0, sticky="w", **pad)
        row += 1
        wrap = ttk.Frame(frm)
        wrap.grid(row=row, column=0, columnspan=3, sticky="nsew", padx=12, pady=(0, 8))
        frm.rowconfigure(row, weight=1)
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.log = tk.Text(wrap, wrap="word", height=14, font=("Consolas", 9),
                           bg="#1E1E28", fg="#D6DBE5", insertbackground="#D6DBE5",
                           relief="flat", padx=10, pady=8)
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.config(yscrollcommand=sb.set, state="disabled")
        row += 1

        ttk.Label(frm, text="生成的宠物包在 build/<前缀>/pet/ —— 安装后无需重启宿主应用",
                  style="Hint.TLabel").grid(row=row, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 6))

        self.write("PetForge 就绪。\n"
                   "① 选一张角色图 → ② 点「生成宠物包」→ ③ 点「安装到 dsh-pet」\n"
                   "编码由 ffmpeg 完成，6 个动作模板约需 2-4 分钟。\n")
        self.root.after(100, self.drain)

    # ---------------------------------------------------------------- 工具

    def write(self, text):
        self.q.put(text)

    def drain(self):
        try:
            while True:
                chunk = self.q.get_nowait()
                self.log.config(state="normal")
                self.log.insert("end", chunk)
                self.log.see("end")
                self.log.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def pick_image(self):
        p = filedialog.askopenfilename(
            title="选择角色图",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp"), ("所有文件", "*.*")])
        if p:
            self.var_img.set(p)

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_gen.config(state=state)
        self.btn_stop.config(state="normal" if busy else "disabled")
        self.btn_inst.config(state="normal" if (not busy and self.last_prefix) else "disabled")

    # ---------------------------------------------------------------- 执行

    def run(self, args, on_done=None):
        self.set_busy(True)
        self.write(f"\n$ {' '.join(str(a) for a in args)}\n")
        self.write("─" * 60 + "\n")

        def worker():
            code = -1
            try:
                self.proc = subprocess.Popen(
                    args, cwd=str(HERE), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                    bufsize=1)
                assert self.proc.stdout is not None
                for line in self.proc.stdout:
                    self.write(line)
                code = self.proc.wait()
            except Exception as e:                      # noqa: BLE001
                self.write(f"\n[错误] {e}\n")
            finally:
                self.proc = None
                self.root.after(0, lambda: self.finish(code, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def finish(self, code, on_done):
        if code == 0:
            self.write("\n✅ 完成\n")
        else:
            self.write(f"\n❌ 退出码 {code}\n")
        self.set_busy(False)
        if code == 0 and on_done:
            on_done()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.write("\n[已请求停止]\n")

    def generate(self):
        img = self.var_img.get().strip()
        if not img or not Path(img).exists():
            messagebox.showwarning("缺少角色图", "请先选一张角色图")
            return
        prefix = self.var_prefix.get().strip() or "mature"
        args = [PY, "forge.py",
                "--input", img,
                "--prefix", prefix,
                "--name", self.var_name.get().strip() or prefix,
                "--height", str(int(self.var_h.get()))]
        if self.var_chroma.get():
            key = self.var_key.get().strip().lstrip("#")
            if len(key) == 6:
                args += ["--key", key]

        def done():
            self.last_prefix = prefix
            self.btn_inst.config(state="normal")

        self.run(args, on_done=done)

    def install(self):
        prefix = self.last_prefix or self.var_prefix.get().strip() or "mature"
        self.run([PY, "install.py", "--prefix", prefix])


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
