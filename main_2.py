import tkinter as tk
import easyocr
from PIL import ImageGrab
import pyautogui, pyperclip, time, os
from PIL import ImageGrab, ImageOps, ImageFilter

# --- 1. OCR 引擎初始化 ---
print("正在启动 EasyOCR 引擎 (P1000 极速版)...")
reader = easyocr.Reader(['ch_sim', 'en'], gpu=False)


class Calibrator:
    """四点校准器：题目区域、输入框、发送按钮、待命点位"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.3, "-fullscreen", True, "-topmost", True)
        self.canvas = tk.Canvas(self.root, cursor="cross", bg="grey")
        self.canvas.pack(fill="both", expand=True)

        self.step = 0
        self.roi = None
        self.input_pos = None
        self.send_pos = None
        self.hover_pos = None

        self.label = tk.Label(self.root, text="1. 划选【题目区域】", font=("微软雅黑", 20), fg="red", bg="white")
        self.label.pack(pady=50)

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

    def on_press(self, event):
        if self.step == 0:
            self.start_x, self.start_y = event.x, event.y
            self.rect = self.canvas.create_rectangle(self.start_x, self.start_y, 1, 1, outline='red', width=2)
        else:
            pos = (event.x, event.y)
            if self.step == 1:
                self.input_pos = pos
                self.label.config(text="3. 点击网页 AI 的【发送按钮】")
                self.step = 2
            elif self.step == 2:
                self.send_pos = pos
                self.label.config(text="4. 点击【鼠标待命位置】(建议靠近题目)")
                self.step = 3
            elif self.step == 3:
                self.hover_pos = pos
                self.root.destroy()

    def on_move(self, event):
        if self.step == 0: self.canvas.coords(self.rect, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event):
        if self.step == 0:
            self.roi = (min(self.start_x, event.x), min(self.start_y, event.y),
                        max(self.start_x, event.x), max(self.start_y, event.y))
            self.step = 1
            self.canvas.delete("all")
            self.label.config(text="2. 点击网页 AI 的【输入框】")


def do_full_auto(roi, input_pos, send_pos, hover_pos):
    """全自动核心逻辑 - 增强防干扰版"""
    start_time = time.time()
    try:
        # 1. 截图
        img = ImageGrab.grab(bbox=roi)

        # --- 图像增强：干掉干扰水印 ---
        # A. 转灰度
        img = img.convert('L')
        # B. 自动对比度增强（拉开文字和水印的差距）
        img = ImageOps.autocontrast(img)
        # C. 二值化处理：核心降噪逻辑
        # 阈值设为 140（可根据实际效果微调，120-160之间）
        # 意义：灰度值小于 140 的变黑(0)，大于 140 的变白(255)
        threshold = 140
        img = img.point(lambda p: 0 if p < threshold else 255)
        # D. 轻微模糊再锐化（可选，用于消除毛刺）
        img = img.filter(ImageFilter.SMOOTH_MORE)

        # 缩小尺寸提速
        w, h = img.size
        img = img.resize((w // 2, h // 2))
        img.save("fast_scan.png")

        # 2. 识别
        result = reader.readtext("fast_scan.png", detail=0)

        # 过滤掉单字符乱码，保留有意义的中文
        clean_list = [res for res in result if len(res) > 1 or res in "ABCD"]
        question = " ".join(clean_list).strip()

        if question:
            pyperclip.copy(question)
            pyautogui.click(input_pos)
            time.sleep(0.1)
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.press('backspace')
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.1)
            pyautogui.click(send_pos)
            pyautogui.moveTo(hover_pos)
            print(f"[{time.strftime('%H:%M:%S')}] 降噪识别成功! 耗时: {time.time() - start_time:.2f}s")
        else:
            print("[!] 区域内未发现文字")
    except Exception as e:
        print(f"[错误] {e}")


def run_app():
    """主程序循环，支持重校"""
    while True:
        # 1. 执行校准
        cali = Calibrator()
        cali.root.mainloop()

        if not (cali.roi and cali.input_pos and cali.send_pos and cali.hover_pos):
            print("校准未完成，程序退出。")
            break

        # 2. 建立控制面板
        panel = tk.Tk()
        panel.title("刷题助手")
        panel.attributes("-topmost", True)
        panel.geometry("280x220+50+50")

        # 定义重校标志位
        recalibrate = [False]

        def trigger_recali():
            recalibrate[0] = True
            panel.destroy()

        # 核心按钮
        tk.Button(panel, text="【 一键同步 AI 】", font=('微软雅黑', 14, 'bold'),
                  bg='#4CAF50', fg='white', height=2,
                  command=lambda: do_full_auto(cali.roi, cali.input_pos, cali.send_pos, cali.hover_pos)).pack(
            fill="both", padx=10, pady=10)

        # 重校按钮
        tk.Button(panel, text="重新矫正区域/坐标", bg="#FF9800", fg="white",
                  command=trigger_recali).pack(fill="x", padx=10, pady=5)

        # 退出按钮
        tk.Button(panel, text="退出脚本", bg="#555555", fg="white",
                  command=lambda: os._exit(0)).pack(fill="x", padx=10, pady=5)

        print("助手就绪！待命位置已记录。")
        panel.mainloop()

        if not recalibrate[0]:
            break


if __name__ == '__main__':
    run_app()
