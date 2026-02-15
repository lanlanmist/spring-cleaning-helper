import os
import tkinter as tk
from paddleocr import PaddleOCR
from PIL import ImageGrab
import numpy as np
import pyperclip
import time

# --- 初始化 (P1000 GPU 极简版) ---
print("正在启动显卡加速 OCR 引擎 (带硬盘调试功能)...")
ocr = PaddleOCR(lang="ch")

# 强制禁用可能干扰截图识别的内部设置
if hasattr(ocr, 'pipeline'):
    ocr.pipeline.use_doc_preprocessor = False
    ocr.pipeline.use_doc_orientation_classify = False


def do_ocr_work(region):
    start_time = time.time()
    try:
        # 1. 截图并保存到硬盘 (检查截图是否正确)
        img = ImageGrab.grab(bbox=region)
        img.save("debug_screenshot.png")  # 保存原始截图

        # 将 PIL 图片转换为 Paddle 能识别的数组
        img_np = np.array(img)

        # 2. 调用识别
        result = ocr.predict(img_np)

        # 3. 提取文字 (深度兼容 3.x 结构)
        txts = []
        if result and isinstance(result, list):
            for item in result:
                # 方案 A: 3.x 标准 rec_texts
                if isinstance(item, dict) and 'rec_texts' in item:
                    txts.extend([str(t) for t in item['rec_texts']])

                # 方案 B: 某些子版本的 doc_res
                elif hasattr(item, 'doc') and 'res' in item.doc:
                    for line in item.doc['res']:
                        if 'text' in line:
                            txts.append(str(line['text']))

        clean_text = "".join(txts).strip()

        if clean_text:
            pyperclip.copy(clean_text)
            elapsed = time.time() - start_time
            print(f"[{time.strftime('%H:%M:%S')}] 识别成功! 耗时: {elapsed:.2f}s")
            print(f"内容预览: {clean_text[:40]}...")
        else:
            print("[!] 识别结果为空。")
            print(f"诊断：截图已存至 {os.getcwd()}\\debug_screenshot.png，请检查图片是否黑屏或显示异常。")

    except Exception as e:
        print(f"[错误] 运行异常: {e}")


# --- UI 选择逻辑 ---
class Selector:
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
        self.selection = (min(self.start_x, event.x), min(self.start_y, event.y), max(self.start_x, event.x),
                          max(self.start_y, event.y))
        self.root.destroy()


if __name__ == '__main__':
    tool = Selector()
    tool.root.mainloop()
    if tool.selection:
        panel = tk.Tk()
        panel.title("P1000 GPU 调试版")
        panel.attributes("-topmost", True)
        panel.geometry("250x150+50+50")
        tk.Button(panel, text="⚡ 截图并识别", font=('微软雅黑', 12, 'bold'), bg='#4CAF50', fg='white',
                  command=lambda: do_ocr_work(tool.selection)).pack(expand=True, fill="both", padx=10, pady=10)
        print("助手就绪！识别失败请检查 debug_screenshot.png")
        panel.mainloop()
