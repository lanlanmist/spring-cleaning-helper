# ================== 必须最先执行 ==================
import os

os.environ["FLAGS_use_onednn"] = "0"
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["PADDLE_DISABLE_PIR"] = "1"

import ctypes

try:
    ctypes.windll.user32.SetProcessDPIAware()
except:
    pass

import tkinter as tk
from tkinter import messagebox
import time
import numpy as np
import cv2
import mss
from paddleocr import PaddleOCR
import pyperclip
import pyautogui

# ================== OCR 初始化 ==================
print("正在热机 PaddleOCR...")
ocr = PaddleOCR(lang="ch", use_angle_cls=False, show_log=False)
print("OCR 就绪\n")


# ================== 交互选择类 ==================
class BaseSelector:
    """通用全屏遮罩基类"""

    def __init__(self, parent, title):
        self.win = tk.Toplevel(parent)
        self.win.attributes("-alpha", 0.3, "-fullscreen", True, "-topmost", True)
        self.canvas = tk.Canvas(self.win, cursor="cross", bg="grey")
        self.canvas.pack(fill="both", expand=True)
        # 顶部提示文字
        tk.Label(self.win, text=f" 【操作提示】：{title} ",
                 fg="yellow", bg="black", font=("微软雅黑", 20, "bold")).place(relx=0.5, rely=0.1, anchor="center")
        self.win.bind("<Escape>", lambda e: self.win.destroy())


class RegionSelector(BaseSelector):
    """区域框选"""

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
                                                 width=3)

    def on_move(self, event):
        self.canvas.coords(self.rect, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event):
        self.selection = (min(self.start_x, event.x), min(self.start_y, event.y), max(self.start_x, event.x),
                          max(self.start_y, event.y))
        self.win.destroy()


class PointSelector(BaseSelector):
    """点选"""

    def __init__(self, parent, title):
        super().__init__(parent, title)
        self.canvas.config(cursor="hand2")
        self.pos = None
        self.canvas.bind("<Button-1>", self.on_click)

    def on_click(self, event):
        self.pos = (event.x_root, event.y_root)
        self.win.destroy()


# ================== 主程序 ==================
class OCRApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("刷题助手 V2.5")
        self.root.geometry("400x650+50+50")
        self.root.attributes("-topmost", True)

        # 存储坐标
        self.roi = None
        self.ai_input_pos = None
        self.ai_send_pos = None
        self.next_btn_pos = None
        self.home_pos = None  # 归位位置

        self.setup_ui()

    def setup_ui(self):
        # 1. 初始化
        tk.Label(self.root, text=" STEP 1: 全局配置 ", fg="#E91E63", font=("微软雅黑", 10, "bold")).pack(pady=10)
        tk.Button(self.root, text="🚀 开始初始化 (顺序设定5个位置)", bg="#FF9800", fg="white",
                  font=("微软雅黑", 10, "bold"), height=2, command=self.full_init).pack(fill="x", padx=40)

        # 2. 核心功能按钮 (绿色)
        tk.Label(self.root, text=" STEP 2: 日常刷题 ", fg="#2196F3", font=("微软雅黑", 10, "bold")).pack(pady=10)
        self.btn_ocr = tk.Button(self.root, text="【 识题 + 自动发送 】", bg="#4CAF50", fg="white",
                                 font=("微软雅黑", 12, "bold"), height=2, command=self.one_key_process)
        self.btn_ocr.pack(fill="x", padx=40, pady=5)

        # 3. 下一题按钮 (样式对齐绿色按钮)
        tk.Frame(self.root, height=1, bg="#ddd").pack(fill="x", padx=40, pady=10)
        tk.Label(self.root, text="点击间隔 (ms):").pack()
        self.delay_input = tk.Entry(self.root, justify='center', font=("Arial", 11))
        self.delay_input.insert(0, "200")
        self.delay_input.pack(pady=5)

        self.btn_next = tk.Button(self.root, text="【 ⏭️ 下一题 】", bg="#4CAF50", fg="white",
                                  font=("微软雅黑", 12, "bold"), height=2, command=self.action_next_question)
        self.btn_next.pack(fill="x", padx=40, pady=5)

        # 4. 修正区域
        tk.Label(self.root, text=" [ 单独位置修正 ] ", fg="grey", font=("微软雅黑", 9)).pack(pady=10)
        fix_frame = tk.Frame(self.root)
        fix_frame.pack()

        btns = [
            ("题目区域", self.select_roi), ("输入框", self.select_ai_input),
            ("发送按键", self.select_ai_send), ("下一题键", self.select_next_btn),
            ("归位点", self.select_home_pos)
        ]

        for i, (name, cmd) in enumerate(btns):
            tk.Button(fix_frame, text=name, width=10, command=cmd).grid(row=i // 2, column=i % 2, padx=5, pady=3)

    # --- 选择逻辑 ---
    def select_roi(self):
        s = RegionSelector(self.root, "用鼠标【拖动框选】题目所在的区域")
        self.root.wait_window(s.win)
        self.roi = s.selection
        if self.roi: print(f"✔ 区域记录成功")

    def select_ai_input(self):
        p = PointSelector(self.root, "请点击【AI输入框】中心")
        self.root.wait_window(p.win)
        self.ai_input_pos = p.pos

    def select_ai_send(self):
        p = PointSelector(self.root, "请点击【AI发送按钮】中心")
        self.root.wait_window(p.win)
        self.ai_send_pos = p.pos

    def select_next_btn(self):
        p = PointSelector(self.root, "请点击【下一题/确认】按钮中心")
        self.root.wait_window(p.win)
        self.next_btn_pos = p.pos

    def select_home_pos(self):
        p = PointSelector(self.root, "请点击【归位位置】(发送完后鼠标停哪儿)")
        self.root.wait_window(p.win)
        self.home_pos = p.pos

    def full_init(self):
        self.select_roi()
        if not self.roi: return
        self.select_ai_input()
        if not self.ai_input_pos: return
        self.select_ai_send()
        if not self.ai_send_pos: return
        self.select_next_btn()
        if not self.next_btn_pos: return
        self.select_home_pos()
        messagebox.showinfo("就绪", "所有位置已锁定，可以开始刷题")

    # --- 功能执行 ---
    def one_key_process(self):
        if not all([self.roi, self.ai_input_pos, self.ai_send_pos]):
            messagebox.showwarning("提示", "请先初始化位置")
            return

        # 1. OCR
        x1, y1, x2, y2 = self.roi
        with mss.mss() as sct:
            shot = np.array(sct.grab({"left": int(x1), "top": int(y1), "width": int(x2 - x1), "height": int(y2 - y1)}))[
                   :, :, :3]

        result = ocr.ocr(shot, cls=False)
        if not result or not result[0]: return

        texts = [line[1][0] for line in result[0]]
        final_text = "\n".join(texts).replace("查看提示", "")

        # 2. 自动化
        pyperclip.copy(final_text)
        pyautogui.click(self.ai_input_pos)
        time.sleep(0.1)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.1)
        pyautogui.click(self.ai_send_pos)

        # 3. 归位
        if self.home_pos:
            pyautogui.moveTo(self.home_pos)
        print("✔ 已发送并归位")

    def action_next_question(self):
        if not self.next_btn_pos:
            messagebox.showwarning("提示", "请先选择下一题位置")
            return

        try:
            delay = int(self.delay_input.get()) / 1000.0
        except:
            delay = 0.2

        pyautogui.click(self.next_btn_pos)
        time.sleep(delay)
        pyautogui.click(self.next_btn_pos)

        # 归位
        if self.home_pos:
            pyautogui.moveTo(self.home_pos)
        print(f"✔ 下一题已点击并归位")


if __name__ == "__main__":
    app = OCRApp()
    app.root.mainloop()
