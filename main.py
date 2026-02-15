import tkinter as tk
from tkinter import scrolledtext
import time
import numpy as np
import mss
import pyperclip
import pyautogui
import keyboard
import threading
from config import ocr
from gui_selectors import RegionSelector, PointSelector

# --- 你的自定义配置 ---
DEFAULT_PROMPT = "请给出这道题的答案。如果单选，给出一个大写字母选项；如果多选，给出如：AB，AC，ABC这样的答案，不要给任何符号：\n"


class OCRApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("大扫除小助手 V1.1 平安是福版")
        self.root.geometry("460x900+50+50")
        self.root.attributes("-topmost", True)

        # 坐标初始化
        self.roi = None
        self.answer_roi = None
        self.ai_input_pos = None
        self.ai_send_pos = None
        self.next_btn_pos = None
        self.user_home_pos = None

        self.setup_ui()
        self.start_hotkey_listener()

    def setup_ui(self):
        # --- STEP 1: 坐标初始化 ---
        tk.Label(self.root, text=" STEP 1: 坐标初始化 ", fg="#E91E63", font=("微软雅黑", 10, "bold")).pack(pady=5)
        tk.Button(self.root, text="🚀 顺序设定所有位置 (含答案区)", bg="#FF9800", fg="white",
                  font=("微软雅黑", 10, "bold"), height=2, command=self.full_init).pack(fill="x", padx=40)

        # --- STEP 2: Prompt 编辑区 ---
        tk.Frame(self.root, height=2, bg="#eee").pack(fill="x", pady=10)
        tk.Label(self.root, text=" STEP 2: AI 指令 (Prompt) ", fg="#009688", font=("微软雅黑", 10, "bold")).pack()
        self.prompt_text = scrolledtext.ScrolledText(self.root, height=4, font=("微软雅黑", 9))
        self.prompt_text.insert(tk.END, DEFAULT_PROMPT)
        self.prompt_text.pack(fill="x", padx=20, pady=5)

        # --- STEP 3: 自动化参数设置 ---
        tk.Frame(self.root, height=2, bg="#eee").pack(fill="x", pady=5)
        param_frame = tk.Frame(self.root)
        param_frame.pack(pady=5)

        tk.Label(param_frame, text="双击间隔(ms):").grid(row=0, column=0)
        self.delay_click = tk.Entry(param_frame, width=8, justify='center')
        self.delay_click.insert(0, "1000")
        self.delay_click.grid(row=0, column=1, padx=5)

        tk.Label(param_frame, text="AI回答等待(ms):").grid(row=1, column=0)
        self.delay_ai_wait = tk.Entry(param_frame, width=8, justify='center')
        self.delay_ai_wait.insert(0, "6000")
        self.delay_ai_wait.grid(row=1, column=1, padx=5, pady=5)

        # --- STEP 4: 快捷键设置 ---
        tk.Label(self.root, text=" STEP 4: 快捷键设置 ", fg="#673AB7", font=("微软雅黑", 10, "bold")).pack()
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
        tk.Button(self.root, text="💾 应用热键", command=self.update_hotkeys, bg="#607D8B", fg="white").pack(pady=5)

        # --- STEP 5: 手动操作区 ---
        tk.Label(self.root, text=" STEP 5: 手动操作区 ", fg="#2196F3", font=("微软雅黑", 10, "bold")).pack(pady=5)
        self.btn_ocr = tk.Button(self.root, text="【 识题 + 自动发送 】", bg="#4CAF50", fg="white",
                                 font=("微软雅黑", 12, "bold"), height=2, command=self.start_ocr_thread)
        self.btn_ocr.pack(fill="x", padx=40, pady=5)

        self.btn_next = tk.Button(self.root, text="【 ⏭️ 下一题 】", bg="#4CAF50", fg="white",
                                  font=("微软雅黑", 12, "bold"), height=2, command=self.action_next_question)
        self.btn_next.pack(fill="x", padx=40, pady=5)

        # --- 位置修正区 ---
        tk.Label(self.root, text=" [ 位置修正 ] ", fg="grey", font=("微软雅黑", 9)).pack(pady=5)
        fix_frame = tk.Frame(self.root)
        fix_frame.pack()
        btns = [("题目区", self.select_roi), ("答案区", self.select_answer_roi), ("输入框", self.select_ai_input),
                ("发送键", self.select_ai_send), ("下一题", self.select_next_btn), ("归位点", self.select_home_pos)]
        for i, (name, cmd) in enumerate(btns):
            tk.Button(fix_frame, text=name, width=8, command=cmd).grid(row=i // 3, column=i % 3, padx=3, pady=3)

    def start_ocr_thread(self):
        threading.Thread(target=self.one_key_process, daemon=True).start()

    def one_key_process(self):
        if not self.roi: return

        current_prompt = self.prompt_text.get("1.0", tk.END).strip() + "\n"
        with mss.mss() as sct:
            x1, y1, x2, y2 = self.roi
            shot = np.array(sct.grab({"left": int(x1), "top": int(y1), "width": int(x2 - x1), "height": int(y2 - y1)}))[
                   :, :, :3]

        result = ocr.ocr(shot, cls=False)
        # --- 修正点：正确的 PaddleOCR 结果提取逻辑 ---
        if result and result[0]:
            lines = [line[1][0] for line in result[0]]  # 提取每一行的文本内容
            original_text = "\n".join(lines).replace("查看提示", "")
            final_text = current_prompt + original_text
            pyperclip.copy(final_text)

            if self.ai_input_pos:
                pyautogui.click(self.ai_input_pos)
                time.sleep(0.1)
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(0.1)
                pyautogui.click(self.ai_send_pos)
                if self.user_home_pos:
                    pyautogui.moveTo(self.user_home_pos)

            try:
                wait_time = int(self.delay_ai_wait.get()) / 1000.0
            except:
                wait_time = 10.0

            print(f"正在等待 AI 回答 ({wait_time}s)...")
            time.sleep(wait_time)
            self.ocr_answer_area()

    def ocr_answer_area(self):
        if not self.answer_roi: return
        with mss.mss() as sct:
            x1, y1, x2, y2 = self.answer_roi
            shot = np.array(sct.grab({"left": int(x1), "top": int(y1), "width": int(x2 - x1), "height": int(y2 - y1)}))[
                   :, :, :3]

        result = ocr.ocr(shot, cls=False)
        # --- 修正点：同步答案区的提取逻辑 ---
        if result and result[0]:
            lines = [line[1][0] for line in result[0]]
            answer_text = "".join(lines)
            print(f">>> [自动检测] AI 答案: {answer_text}")

    def action_next_question(self):
        if not self.next_btn_pos: return
        try:
            delay = int(self.delay_click.get()) / 1000.0
        except:
            delay = 1.0
        pyautogui.click(self.next_btn_pos)
        time.sleep(delay)
        pyautogui.click(self.next_btn_pos)

        self.root.update()
        btn_pos = (self.btn_ocr.winfo_rootx() + self.btn_ocr.winfo_width() // 2,
                   self.btn_ocr.winfo_rooty() + self.btn_ocr.winfo_height() // 2)
        pyautogui.moveTo(btn_pos)

    def update_hotkeys(self):
        keyboard.unhook_all()
        try:
            keyboard.add_hotkey(self.hk_ocr_entry.get().strip(), self.start_ocr_thread)
            keyboard.add_hotkey(self.hk_next_entry.get().strip(), self.action_next_question)
            print("热键更新成功")
        except:
            pass

    def start_hotkey_listener(self):
        threading.Thread(target=self.update_hotkeys, daemon=True).start()

    def select_roi(self):
        s = RegionSelector(self.root, "框选【题目区域】")
        self.root.wait_window(s.win)
        if not s.cancelled: self.roi = s.selection; return True
        return False

    def select_answer_roi(self):
        s = RegionSelector(self.root, "框选【AI 答案显示区域】")
        self.root.wait_window(s.win)
        if not s.cancelled: self.answer_roi = s.selection; return True
        return False

    def select_ai_input(self):
        p = PointSelector(self.root, "点击【AI 输入框】")
        self.root.wait_window(p.win)
        if not p.cancelled: self.ai_input_pos = p.pos; return True
        return False

    def select_ai_send(self):
        p = PointSelector(self.root, "点击【AI 发送按钮】")
        self.root.wait_window(p.win)
        if not p.cancelled: self.ai_send_pos = p.pos; return True
        return False

    def select_next_btn(self):
        p = PointSelector(self.root, "点击【下一题】按钮")
        self.root.wait_window(p.win)
        if not p.cancelled: self.next_btn_pos = p.pos; return True
        return False

    def select_home_pos(self):
        p = PointSelector(self.root, "点击【鼠标归位点】")
        self.root.wait_window(p.win)
        if not p.cancelled: self.user_home_pos = p.pos; return True
        return False

    def full_init(self):
        steps = [self.select_roi, self.select_answer_roi, self.select_ai_input,
                 self.select_ai_send, self.select_next_btn, self.select_home_pos]
        for step_func in steps:
            if not step_func(): break


if __name__ == "__main__":
    app = OCRApp()
    app.root.mainloop()
