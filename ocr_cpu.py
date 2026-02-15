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
import keyboard  # 用于全局快捷键
import threading  # 防止快捷键卡死UI

# ================== OCR 初始化 ==================
print("正在热机 PaddleOCR...")
ocr = PaddleOCR(lang="ch", use_angle_cls=False, show_log=False)
print("OCR 就绪\n")


# ================== 交互选择类 (增强字体显示) ==================
class BaseSelector:
    def __init__(self, parent, title):
        self.win = tk.Toplevel(parent)
        self.win.attributes("-alpha", 0.3, "-fullscreen", True, "-topmost", True)
        self.canvas = tk.Canvas(self.win, cursor="cross", bg="grey")
        self.canvas.pack(fill="both", expand=True)

        # 优化：在遮罩层上层再放一个全透明文字层，确保文字 100% 不透明
        self.label = tk.Label(self.win, text=title,
                              fg="#00FF00", bg="black",  # 绿底黑字，对比度最高
                              font=("微软雅黑", 32, "bold"),
                              relief="raised", borderwidth=5)
        self.label.place(relx=0.5, rely=0.15, anchor="center")
        self.win.bind("<Escape>", lambda e: self.win.destroy())


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
        self.selection = (min(self.start_x, event.x), min(self.start_y, event.y), max(self.start_x, event.x),
                          max(self.start_y, event.y))
        self.win.destroy()


class PointSelector(BaseSelector):
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
        self.root.title("大扫除小助手 V1.0 花开富贵版")
        self.root.geometry("420x720+50+50")
        self.root.attributes("-topmost", True)

        self.roi = None
        self.ai_input_pos = None
        self.ai_send_pos = None
        self.next_btn_pos = None
        self.user_home_pos = None

        self.setup_ui()
        self.start_hotkey_listener()

    def get_software_btn_pos(self):
        self.root.update()
        return (self.btn_ocr.winfo_rootx() + self.btn_ocr.winfo_width() // 2,
                self.btn_ocr.winfo_rooty() + self.btn_ocr.winfo_height() // 2)

    def setup_ui(self):
        # 1. 配置
        tk.Label(self.root, text=" STEP 1: 坐标初始化 ", fg="#E91E63", font=("微软雅黑", 10, "bold")).pack(pady=10)
        tk.Button(self.root, text="🚀 顺序设定所有位置", bg="#FF9800", fg="white",
                  font=("微软雅黑", 10, "bold"), height=2, command=self.full_init).pack(fill="x", padx=40)

        # 2. 快捷键自定义
        tk.Frame(self.root, height=2, bg="#eee").pack(fill="x", pady=10)
        tk.Label(self.root, text=" STEP 2: 快捷键设置 (键盘直接按) ", fg="#673AB7",
                 font=("微软雅黑", 10, "bold")).pack()

        hk_frame = tk.Frame(self.root)
        hk_frame.pack(pady=5)

        tk.Label(hk_frame, text="识题热键:").grid(row=0, column=0)
        self.hk_ocr_entry = tk.Entry(hk_frame, width=10, justify='center')
        self.hk_ocr_entry.insert(0, "f1")
        self.hk_ocr_entry.grid(row=0, column=1, padx=5)

        tk.Label(hk_frame, text="下一题热键:").grid(row=1, column=0)
        self.hk_next_entry = tk.Entry(hk_frame, width=10, justify='center')
        self.hk_next_entry.insert(0, "f2")
        self.hk_next_entry.grid(row=1, column=1, padx=5)

        tk.Button(self.root, text="💾 保存并应用热键", command=self.update_hotkeys, bg="#607D8B", fg="white").pack(
            pady=5)

        # 3. 核心按钮
        tk.Label(self.root, text=" STEP 3: 手动操作区 ", fg="#2196F3", font=("微软雅黑", 10, "bold")).pack(pady=10)
        self.btn_ocr = tk.Button(self.root, text="【 识题 + 自动发送 】", bg="#4CAF50", fg="white",
                                 font=("微软雅黑", 12, "bold"), height=2, command=self.one_key_process)
        self.btn_ocr.pack(fill="x", padx=40, pady=5)

        tk.Label(self.root, text="双击间隔(ms):").pack()
        self.delay_input = tk.Entry(self.root, justify='center', font=("Arial", 11), width=10)
        self.delay_input.insert(0, "500")
        self.delay_input.pack(pady=5)

        self.btn_next = tk.Button(self.root, text="【 ⏭️ 下一题 】", bg="#4CAF50", fg="white",
                                  font=("微软雅黑", 12, "bold"), height=2, command=self.action_next_question)
        self.btn_next.pack(fill="x", padx=40, pady=5)

        # 4. 重选区
        tk.Label(self.root, text=" [ 位置修正 ] ", fg="grey", font=("微软雅黑", 9)).pack(pady=10)
        fix_frame = tk.Frame(self.root)
        fix_frame.pack()
        btns = [("题目区", self.select_roi), ("输入框", self.select_ai_input), ("发送键", self.select_ai_send),
                ("下一题", self.select_next_btn), ("归位点", self.select_home_pos)]
        for i, (name, cmd) in enumerate(btns):
            tk.Button(fix_frame, text=name, width=8, command=cmd).grid(row=i // 3, column=i % 3, padx=3, pady=3)

    # --- 快捷键逻辑 ---
    def start_hotkey_listener(self):
        # 使用线程监听，避免阻塞主窗口
        def listen():
            self.update_hotkeys()

        threading.Thread(target=listen, daemon=True).start()

    def update_hotkeys(self):
        keyboard.unhook_all()
        ocr_key = self.hk_ocr_entry.get().strip()
        next_key = self.hk_next_entry.get().strip()
        try:
            keyboard.add_hotkey(ocr_key, self.one_key_process)
            keyboard.add_hotkey(next_key, self.action_next_question)
            print(f"热键已更新: 识题={ocr_key}, 下一题={next_key}")
        except:
            print("热键格式错误")

    # --- 选择逻辑 ---
    def select_roi(self):
        s = RegionSelector(self.root, "【第一步】：拖动鼠标框选题目区域")
        self.root.wait_window(s.win)
        self.roi = s.selection

    def select_ai_input(self):
        p = PointSelector(self.root, "【第二步】：点击 AI 输入框位置")
        self.root.wait_window(p.win)
        self.ai_input_pos = p.pos

    def select_ai_send(self):
        p = PointSelector(self.root, "【第三步】：点击 AI 发送按钮位置")
        self.root.wait_window(p.win)
        self.ai_send_pos = p.pos

    def select_next_btn(self):
        p = PointSelector(self.root, "【第四步】：点击 下一题/确认 按钮位置")
        self.root.wait_window(p.win)
        self.next_btn_pos = p.pos

    def select_home_pos(self):
        p = PointSelector(self.root, "【第五步】：选择 自定义归位点 (看题位置)")
        self.root.wait_window(p.win)
        self.user_home_pos = p.pos

    def full_init(self):
        self.select_roi();
        self.select_ai_input();
        self.select_ai_send();
        self.select_next_btn();
        self.select_home_pos()

    # --- 核心动作 ---
    def one_key_process(self):
        if not self.roi: return
        x1, y1, x2, y2 = self.roi
        with mss.mss() as sct:
            shot = np.array(sct.grab({"left": int(x1), "top": int(y1), "width": int(x2 - x1), "height": int(y2 - y1)}))[
                   :, :, :3]
        result = ocr.ocr(shot, cls=False)
        if result and result[0]:
            final_text = "\n".join([line[1][0] for line in result[0]]).replace("查看提示", "")
            pyperclip.copy(final_text)
            if self.ai_input_pos:
                pyautogui.click(self.ai_input_pos)
                time.sleep(0.05)
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(0.05)
                pyautogui.click(self.ai_send_pos)
                if self.user_home_pos:
                    pyautogui.moveTo(self.user_home_pos)

    def action_next_question(self):
        if not self.next_btn_pos: return
        try:
            delay = int(self.delay_input.get()) / 1000.0
        except:
            delay = 0.2
        pyautogui.click(self.next_btn_pos)
        time.sleep(delay)
        pyautogui.click(self.next_btn_pos)
        # 点击完下一题，鼠标自动回到软件的“识题”按钮
        pyautogui.moveTo(self.get_software_btn_pos())


if __name__ == "__main__":
    app = OCRApp()
    app.root.mainloop()
