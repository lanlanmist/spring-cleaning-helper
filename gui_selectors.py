import tkinter as tk


class BaseSelector:
    def __init__(self, parent, title):
        self.win = tk.Toplevel(parent)
        self.win.attributes("-alpha", 0.3, "-fullscreen", True, "-topmost", True)
        self.cancelled = True

        self.canvas = tk.Canvas(self.win, cursor="cross", bg="grey", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.label = tk.Label(self.win, text=title + "\n(按 Esc 退出本项选择)",
                              fg="#00FF00", bg="black",
                              font=("微软雅黑", 28, "bold"),
                              relief="raised", borderwidth=5)
        self.label.place(relx=0.5, rely=0.15, anchor="center")

        # --- 关键修正：确保窗口获得焦点并拦截所有输入 ---
        self.win.focus_force()  # 强制获得焦点
        self.win.grab_set()  # 锁定所有事件到本窗口，直到它销毁

        # 绑定 Esc 键（兼容大小写）
        self.win.bind("<Escape>", self.on_cancel)

    def on_cancel(self, event=None):
        self.cancelled = True
        self.win.destroy()


class RegionSelector(BaseSelector):
    def __init__(self, parent, title):
        super().__init__(parent, title)
        self.selection = None
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

    def on_press(self, event):
        self.start_x, self.start_y = event.x, event.y
        self.rect = self.canvas.create_rectangle(self.start_x, self.start_y, self.start_x, self.start_y, outline="red",
                                                 width=4)

    def on_move(self, event):
        self.canvas.coords(self.rect, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event):
        self.selection = (min(self.start_x, event.x), min(self.start_y, event.y),
                          max(self.start_x, event.x), max(self.start_y, event.y))
        self.cancelled = False  # 正常完成了选择
        self.win.destroy()


class PointSelector(BaseSelector):
    def __init__(self, parent, title):
        super().__init__(parent, title)
        self.canvas.config(cursor="hand2")
        self.pos = None
        self.canvas.bind("<Button-1>", self.on_click)

    def on_click(self, event):
        self.pos = (event.x_root, event.y_root)
        self.cancelled = False  # 正常完成了选择
        self.win.destroy()
