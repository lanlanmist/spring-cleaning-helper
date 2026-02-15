# ================== 必须最先执行（非常重要） ==================
import os

# 强制使用 Paddle 旧执行路径，避免任何 PIR / oneDNN
os.environ["FLAGS_use_onednn"] = "0"
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["PADDLE_DISABLE_PIR"] = "1"

# Windows DPI 修正（截图不偏移）
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

# ================== OCR 初始化 ==================
print("正在热机 PaddleOCR（CPU 稳定模式）...")

ocr = PaddleOCR(
    lang="ch",
    use_angle_cls=False,   # 刷题场景基本不需要角度分类
    det_db_thresh=0.25,    # 稍微降低阈值，对抗水印干扰
    det_db_box_thresh=0.5,
    det_db_unclip_ratio=1.6
)

print("OCR 就绪\n")


# ================== 区域选择工具 ==================
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


# ================== OCR 主逻辑 ==================
def do_ocr(region):
    print("\n================ OCR START ================")
    t0 = time.time()

    try:
        x1, y1, x2, y2 = region
        w, h = x2 - x1, y2 - y1

        # --- 截图 ---
        with mss.mss() as sct:
            img = np.array(
                sct.grab({
                    "left": int(x1),
                    "top": int(y1),
                    "width": int(w),
                    "height": int(h)
                })
            )[:, :, :3]

        # --- 轻量预处理（不破坏文字）---
        img = cv2.resize(
            img,
            (int(w / 1.4), int(h / 1.4)),
            interpolation=cv2.INTER_LINEAR
        )

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 轻微去噪，尽量压制“小黑点”
        gray = cv2.GaussianBlur(gray, (3, 3), 0)

        # --- OCR ---
        result = ocr.ocr(gray, cls=False)

        texts = []

        if result and isinstance(result, list) and len(result) > 0:
            lines = result[0]  # ← 关键修复点

            for line in lines:
                try:
                    text = line[1][0]
                    score = line[1][1]

                    if isinstance(text, str) and text.strip():
                        texts.append(text)

                except Exception as e:
                    continue

        final_text = "\n".join(texts)

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

    except Exception as e:
        print("[错误]", repr(e))
        print("================ OCR END =================\n")


# ================== 主程序 ==================
if __name__ == "__main__":
    print(">>> 刷题 OCR 助手（CPU 稳定最终版） <<<")

    selector = Selector()
    selector.root.mainloop()

    roi = selector.selection
    if not roi:
        print("未选择区域，退出")
        exit()

    panel = tk.Tk()
    panel.title("刷题 OCR 助手")
    panel.geometry("300x200+50+50")
    panel.attributes("-topmost", True)

    tk.Label(panel, text="已锁定题目区域", fg="blue").pack(pady=8)

    tk.Button(
        panel,
        text="【立即识别】",
        font=("微软雅黑", 12, "bold"),
        bg="#4CAF50",
        fg="white",
        height=3,
        command=lambda: do_ocr(roi)
    ).pack(fill="both", padx=10, pady=5)

    tk.Button(
        panel,
        text="退出",
        bg="#f44336",
        fg="white",
        command=lambda: os._exit(0)
    ).pack(fill="x", padx=10, pady=8)

    print("助手已就绪")
    panel.mainloop()
