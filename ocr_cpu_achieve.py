# ================== 必须最先执行 ==================
import os
os.environ["FLAGS_use_onednn"] = "0"
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["PADDLE_DISABLE_PIR"] = "1"

import ctypes
ctypes.windll.user32.SetProcessDPIAware()

# ================== 常规导入 ==================
import tkinter as tk
import time
import numpy as np
import cv2
import mss
from paddleocr import PaddleOCR
import pyperclip
import pyautogui

# ================== OCR 初始化 ==================
print("正在热机 PaddleOCR（CPU 稳定模式）...")
ocr = PaddleOCR(
    lang="ch",
    use_angle_cls=False,
    det_db_thresh=0.25,
    det_db_box_thresh=0.5,
    det_db_unclip_ratio=1.6
)
print("OCR 就绪\n")

# ================== 区域选择器 ==================
class Selector:
    def __init__(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.3, "-fullscreen", True, "-topmost", True)
        self.canvas = tk.Canvas(self.root, cursor="cross", bg="grey")
        self.canvas.pack(fill="both", expand=True)

        self.start_x = self.start_y = None
        self.rect = None
        self.selection = None

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Escape>", lambda e: self.root.destroy())

    def on_press(self, event):
        self.start_x, self.start_y = event.x, event.y
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y,
            self.start_x, self.start_y,
            outline="red", width=2
        )

    def on_move(self, event):
        self.canvas.coords(
            self.rect,
            self.start_x, self.start_y,
            event.x, event.y
        )

    def on_release(self, event):
        self.selection = (
            min(self.start_x, event.x),
            min(self.start_y, event.y),
            max(self.start_x, event.x),
            max(self.start_y, event.y)
        )
        self.root.destroy()

# ================== 单点选择器 ==================
class PointSelector:
    def __init__(self, title="请选择位置"):
        self.pos = None
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.3, "-fullscreen", True, "-topmost", True)
        self.canvas = tk.Canvas(self.root, cursor="hand2", bg="grey")
        self.canvas.pack(fill="both", expand=True)

        self.label = tk.Label(
            self.root,
            text=title,
            fg="yellow",
            bg="black",
            font=("微软雅黑", 16, "bold")
        )
        self.label.place(relx=0.5, rely=0.05, anchor="center")

        self.canvas.bind("<Button-1>", self.on_click)
        self.root.bind("<Escape>", lambda e: self.root.destroy())

    def on_click(self, event):
        self.pos = (event.x_root, event.y_root)
        self.root.destroy()

# ================== OCR 主逻辑 ==================
def do_ocr(region):
    print("\n================ OCR START ================")
    t0 = time.time()
    try:
        x1, y1, x2, y2 = region
        w, h = x2 - x1, y2 - y1
        with mss.mss() as sct:
            img = np.array(sct.grab({
                "left": int(x1),
                "top": int(y1),
                "width": int(w),
                "height": int(h)
            }))[:, :, :3]
        img = cv2.resize(img, (int(w/1.4), int(h/1.4)), interpolation=cv2.INTER_LINEAR)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3,3),0)
        result = ocr.ocr(gray, cls=False)
        texts = []
        if result and isinstance(result, list) and len(result) > 0:
            lines = result[0]
            for line in lines:
                try:
                    text = line[1][0]
                    if isinstance(text, str) and text.strip():
                        texts.append(text)
                except:
                    continue
        final_text = "\n".join(texts)
        if final_text.endswith("查看提示"):
            final_text = final_text[:-4]
        cost = time.time() - t0
        print(f"OCR COST: {cost:.2f}s")
        if final_text:
            print("\n识别结果 ↓↓↓\n")
            print(final_text)
            pyperclip.copy(final_text)
            print("\n[√] 已复制到剪贴板")
        else:
            print("\n[!] 未识别到有效文字")
        print("================ OCR END =================\n")
        return final_text
    except Exception as e:
        print("[错误]", repr(e))
        print("================ OCR END =================\n")
        return ""

# ================== 全局位置变量 ==================
roi = None
ai_input_pos = None
ai_send_pos = None
option_pos = None

# ================== 选择/重选函数 ==================
def select_roi():
    global roi
    selector = Selector()
    selector.root.mainloop()
    roi = selector.selection
    if roi:
        print("✔ 题目区域已选择")

def select_ai_input():
    global ai_input_pos
    ps = PointSelector("请点击【AI 输入框】")
    ps.root.mainloop()
    ai_input_pos = ps.pos
    if ai_input_pos:
        print(f"✔ AI 输入框位置：{ai_input_pos}")

def select_ai_send():
    global ai_send_pos
    ps = PointSelector("请点击【AI 发送按钮】")
    ps.root.mainloop()
    ai_send_pos = ps.pos
    if ai_send_pos:
        print(f"✔ AI 发送按钮位置：{ai_send_pos}")

def select_option_pos():
    global option_pos
    ps = PointSelector("请点击【答案区域位置】")
    ps.root.mainloop()
    option_pos = ps.pos
    if option_pos:
        print(f"✔ 答案位置：{option_pos}")

def select_all():
    select_roi()
    select_ai_input()
    select_ai_send()
    select_option_pos()

# ================== 自动操作函数 ==================
def auto_paste_and_click():
    if not all([roi, ai_input_pos, ai_send_pos, option_pos]):
        print("[!] 请先选择所有区域和位置")
        return
    text = do_ocr(roi)
    if not text:
        print("[!] OCR 未识别到文字，无法粘贴")
        return
    # 点击 AI 输入框
    pyautogui.click(ai_input_pos)
    time.sleep(0.1)
    pyautogui.hotkey('ctrl','v')
    time.sleep(0.1)
    # 点击发送按钮
    pyautogui.click(ai_send_pos)
    time.sleep(0.1)
    # 移动鼠标到答案位置
    pyautogui.moveTo(option_pos)
    print("[√] 已完成自动粘贴、发送、移动到答案位置")

# ================== GUI 主程序 ==================
if __name__ == "__main__":
    print(">>> 刷题 OCR 助手（全自动版） <<<")
    select_all()

    panel = tk.Tk()
    panel.title("刷题 OCR 助手")
    panel.geometry("360x350+50+50")
    panel.attributes("-topmost", True)

    tk.Label(panel, text="已锁定区域和位置", fg="blue").pack(pady=6)

    tk.Button(panel, text="【立即识别 OCR】", font=("微软雅黑",12,"bold"), bg="#4CAF50", fg="white",
              height=2, command=lambda: do_ocr(roi)).pack(fill="both", padx=10, pady=5)
    tk.Button(panel, text="【自动粘贴 + 发送 + 移动鼠标】", font=("微软雅黑",12,"bold"), bg="#009688", fg="white",
              height=2, command=auto_paste_and_click).pack(fill="both", padx=10, pady=5)

    # 重选按钮
    tk.Label(panel, text="【单独重选】", fg="purple").pack(pady=4)
    tk.Button(panel, text="重选题目区域", bg="#2196F3", fg="white", command=select_roi).pack(fill="x", padx=10, pady=2)
    tk.Button(panel, text="重选AI输入框", bg="#2196F3", fg="white", command=select_ai_input).pack(fill="x", padx=10, pady=2)
    tk.Button(panel, text="重选发送按钮", bg="#2196F3", fg="white", command=select_ai_send).pack(fill="x", padx=10, pady=2)
    tk.Button(panel, text="重选答案位置", bg="#2196F3", fg="white", command=select_option_pos).pack(fill="x", padx=10, pady=2)

    tk.Button(panel, text="退出", bg="#f44336", fg="white", command=lambda: os._exit(0)).pack(fill="x", padx=10, pady=8)

    print("助手已就绪")
    panel.mainloop()
