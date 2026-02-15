import tkinter as tk
from paddleocr import PaddleOCR
from PIL import ImageGrab
import pyperclip
import time
import os

# --- 1. OCR 引擎初始化 (只在启动时运行一次) ---
print("正在热机，请稍候...")
# 删除了所有容易报错的参数，确保 P1000 稳定启动
ocr = PaddleOCR(lang="ch", enable_mkldnn=False)


class Selector:
    """划选工具：仅用于开局确定一次位置"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.3, "-fullscreen", True, "-topmost", True)
        self.canvas = tk.Canvas(self.root, cursor="cross", bg="grey")
        self.canvas.pack(fill="both", expand=True)
        self.start_x = self.start_y = self.rect = self.selection = None
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Escape>", lambda e: self.root.destroy())

    def on_press(self, event):
        self.start_x, self.start_y = event.x, event.y
        self.rect = self.canvas.create_rectangle(self.start_x, self.start_y, 1, 1, outline='red', width=2)

    def on_move(self, event):
        self.canvas.coords(self.rect, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event):
        self.selection = (min(self.start_x, event.x), min(self.start_y, event.y),
                          max(self.start_x, event.x), max(self.start_y, event.y))
        self.root.destroy()


def do_ocr_work(region):
    """手动触发的识别逻辑"""
    start_time = time.time()
    try:
        # 1. 截图
        img = ImageGrab.grab(bbox=region)
        # 2. 适度缩小提升 P1000 识别速度 (1.5倍缩小)
        w, h = img.size
        small_img = img.resize((int(w / 1.5), int(h / 1.5)))
        small_img.save("manual_scan.png")

        # 3. 识别
        result = ocr.ocr("manual_scan.png")

        # 4. 提取文字 (适配 3.0b2 字典结构)
        txts = []
        if isinstance(result, list) and len(result) > 0:
            data = result[0]
            if isinstance(data, dict) and 'rec_texts' in data:
                txts = data['rec_texts']

        clean_text = " ".join(txts).strip()

        if clean_text:
            pyperclip.copy(clean_text)
            elapsed = time.time() - start_time
            print(f"\n[√] 识别成功 (耗时: {elapsed:.2f}s)")
            print(f"已复制: {clean_text[:40]}...")
        else:
            print("\n[!] 未发现文字，请确认 Scrcpy 窗口未被遮挡。")

    except Exception as e:
        print(f"\n[错误] 识别失败: {e}")


if __name__ == '__main__':
    print("\n>>> 荣耀刷题助手 (手动高效版) <<<")
    # 1. 区域划选
    tool = Selector()
    tool.root.mainloop()

    roi = tool.selection
    if roi:
        # 2. 建立主控制面板 (常驻置顶)
        panel = tk.Tk()
        panel.title("刷题助手")
        panel.attributes("-topmost", True)
        panel.geometry("300x200+50+50")  # 窗口大一点好点

        tk.Label(panel, text="已锁定 Scrcpy 区域", fg="blue").pack(pady=5)

        # 核心功能按钮：点一下识别一下
        btn_scan = tk.Button(
            panel,
            text="【 立即识别题目 】",
            font=('微软雅黑', 14, 'bold'),
            bg='#4CAF50',
            fg='white',
            height=3,
            command=lambda: do_ocr_work(roi)
        )
        btn_scan.pack(expand=True, fill="both", padx=10, pady=5)

        # 退出按钮
        tk.Button(panel, text="退出脚本", bg="#f44336", fg="white",
                  command=lambda: os._exit(0)).pack(fill="x", padx=10, pady=5)

        print(f"助手已就绪！监控区域: {roi}")
        panel.mainloop()
    else:
        print("未划选区域，程序已退出。")
