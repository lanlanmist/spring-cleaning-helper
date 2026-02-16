import tkinter as tk
from tkinter import scrolledtext
import time
import numpy as np
import mss
import pyperclip
import pyautogui
import keyboard
import threading
import re
from config import ocr
from gui_selectors import RegionSelector, PointSelector

# --- 全局常量 ---
DEFAULT_PROMPT = "请给出这道题的答案。如果单选，给出一个大写字母选项；如果多选，选项间用逗号隔开。忽略最后的“查看提示”：\n"


class OCRApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("大扫除小助手 V2.1")
        self.root.geometry("520x850+800+50")
        self.root.attributes("-topmost", True)

        self.roi = None
        self.answer_roi = None
        self.ai_input_pos = None
        self.ai_send_pos = None
        self.next_btn_pos = None
        self.user_home_pos = None

        self.is_running = False
        self.stop_requested = False
        self.current_options_map = {}

        self.setup_ui()
        self.start_hotkey_listener()

    def log(self, message):
        self.log_area.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_area.see(tk.END)
        print(message)

    def setup_ui(self):
        # --- STEP 1: 坐标初始化 ---
        tk.Label(self.root, text=" STEP 1: 坐标初始化 ", fg="#E91E63", font=("微软雅黑", 10, "bold")).pack(pady=5)
        tk.Button(self.root, text="🚀 顺序设定所有位置", bg="#FF9800", fg="white",
                  font=("微软雅黑", 10, "bold"), height=2, command=self.full_init).pack(fill="x", padx=40)

        # --- STEP 2: AI 指令 ---
        tk.Label(self.root, text=" STEP 2: AI 指令 (Prompt) ", fg="#009688", font=("微软雅黑", 10, "bold")).pack(pady=5)
        self.prompt_text = scrolledtext.ScrolledText(self.root, height=4, font=("微软雅黑", 9))
        self.prompt_text.insert(tk.END, DEFAULT_PROMPT)
        self.prompt_text.pack(fill="x", padx=20)

        # --- STEP 3: 自动化参数设置 ---
        tk.Frame(self.root, height=2, bg="#eee").pack(fill="x", pady=10)
        param_frame = tk.Frame(self.root)
        param_frame.pack()

        # 延时参数
        tk.Label(param_frame, text="AI回答等待(ms):").grid(row=0, column=0)
        self.delay_ai_wait = tk.Entry(param_frame, width=8, justify='center');
        self.delay_ai_wait.insert(0, "10000")
        self.delay_ai_wait.grid(row=0, column=1, padx=5)

        tk.Label(param_frame, text="多选/连击间隔(ms):").grid(row=0, column=2)
        self.delay_auto_click = tk.Entry(param_frame, width=8, justify='center');
        self.delay_auto_click.insert(0, "2000")
        self.delay_auto_click.grid(row=0, column=3, padx=5)

        tk.Label(param_frame, text="操作间微延迟(ms):").grid(row=1, column=0, pady=5)
        self.delay_step = tk.Entry(param_frame, width=8, justify='center');
        self.delay_step.insert(0, "300")
        self.delay_step.grid(row=1, column=1)
        tk.Label(param_frame, text="（移动与点击间缓冲）", fg="grey", font=("微软雅黑", 8)).grid(row=1, column=2,
                                                                                               columnspan=2)

        # 停止词
        tk.Label(self.root, text="停止词设置 (,逗号隔开):", fg="#795548", font=("微软雅黑", 9)).pack()
        self.stop_words_entry = tk.Entry(self.root, width=50, justify='center')
        self.stop_words_entry.insert(0, "再来一组")
        self.stop_words_entry.pack(pady=2)

        # 是否启用停止词停止
        self.stop_on_word_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.root, text="遇到停止词时自动停止",
                       variable=self.stop_on_word_var,
                       font=("微软雅黑", 9),
                       fg="#795548").pack()

        # 捕获特定文字点击功能
        tk.Label(self.root, text="检测到文字时点击（默认：再来一组）:", font=("微软雅黑", 9)).pack()

        self.trigger_word_entry = tk.Entry(self.root, width=30, justify='center')
        self.trigger_word_entry.insert(0, "再来一组")
        self.trigger_word_entry.pack(pady=2)

        param_extra = tk.Frame(self.root)
        param_extra.pack(pady=5)

        tk.Label(param_extra, text="触发点击延时(ms):").grid(row=0, column=0)
        self.delay_trigger_click = tk.Entry(param_extra, width=8, justify='center')
        self.delay_trigger_click.insert(0, "3000")
        self.delay_trigger_click.grid(row=0, column=1, padx=5)

        tk.Label(param_extra, text="点击几次后停止:").grid(row=0, column=2)
        self.max_trigger_times = tk.Entry(param_extra, width=8, justify='center')
        self.max_trigger_times.insert(0, "5")
        self.max_trigger_times.grid(row=0, column=3, padx=5)

        self.loop_var = tk.BooleanVar(value=True)
        # tk.Checkbutton(self.root, text="开启自动循环模式", variable=self.loop_var, font=("微软雅黑", 10, "bold"),
        #                fg="#FF5722").pack()

        # --- STEP 4 & 5 ---
        hk_frame = tk.Frame(self.root);
        hk_frame.pack(pady=5)
        self.hk_ocr_entry = tk.Entry(hk_frame, width=8, justify='center');
        self.hk_ocr_entry.insert(0, "f1");
        self.hk_ocr_entry.grid(row=0, column=0, padx=5)
        self.hk_next_entry = tk.Entry(hk_frame, width=8, justify='center');
        self.hk_next_entry.insert(0, "f2");
        self.hk_next_entry.grid(row=0, column=1, padx=5)
        tk.Button(self.root, text="💾 应用热键", command=self.update_hotkeys).pack()

        self.btn_run = tk.Button(self.root, text="【 F1：启动全自动任务 】", bg="#4CAF50", fg="white",
                                 font=("微软雅黑", 12, "bold"), height=2, command=self.start_automation)
        self.btn_run.pack(fill="x", padx=40, pady=5)
        tk.Button(self.root, text="紧急停止 (Esc)", bg="#f44336", fg="white", command=self.stop_task).pack(fill="x",
                                                                                                           padx=100)

        self.log_area = scrolledtext.ScrolledText(self.root, height=10, font=("Consolas", 9), bg="#f8f8f8")
        self.log_area.pack(fill="x", padx=20, pady=5)

        fix_frame = tk.Frame(self.root);
        fix_frame.pack(pady=5)
        btns = [("题目区", self.select_roi), ("答案区", self.select_answer_roi), ("输入框", self.select_ai_input),
                ("发送键", self.select_ai_send), ("下一题", self.select_next_btn), ("归位点", self.select_home_pos)]
        for i, (name, cmd) in enumerate(btns): tk.Button(fix_frame, text=name, width=8, command=cmd).grid(row=i // 3,
                                                                                                          column=i % 3,
                                                                                                          padx=2,
                                                                                                          pady=2)

    # --- 拟人化点击工具 ---
    def human_click(self, pos):
        """执行 移动 -> 停顿 -> 点击 -> 停顿 的流程"""
        try:
            step_delay = int(self.delay_step.get()) / 1000.0
        except:
            step_delay = 0.3

        pyautogui.moveTo(pos[0], pos[1], duration=step_delay)  # 平滑移动
        time.sleep(step_delay)
        pyautogui.mouseDown()
        time.sleep(0.05)
        pyautogui.mouseUp()
        time.sleep(step_delay)

    # --- 核心流程 ---
    def start_automation(self):
        if self.is_running: return
        self.is_running = True
        self.stop_requested = False
        self.trigger_click_count = 0    # ✅ 只在启动时清零
        threading.Thread(target=self.main_loop, daemon=True).start()

    def main_loop(self):
        while self.is_running and not self.stop_requested:
            if not self.one_key_process() or not self.loop_var.get(): break
            time.sleep(1.0)
        self.is_running = False
        self.log(">>> 自动化已安全停止。")

    def one_key_process(self):
        if not self.roi: return False
        self.current_options_map = {}

        # 1. 识题
        with mss.mss() as sct:
            x1, y1, x2, y2 = self.roi
            shot = np.array(sct.grab({"left": int(x1), "top": int(y1), "width": int(x2 - x1), "height": int(y2 - y1)}))[
                   :, :, :3]

        result = ocr.ocr(shot, cls=False)
        if not result or not result[0]: return False

        lines_text = []
        has_options = False
        stop_words = [w.strip() for w in self.stop_words_entry.get().split(',') if w.strip()]
        trigger_word = self.trigger_word_entry.get().strip()

        for line in result[0]:
            box, (text, conf) = line
            lines_text.append(text)

            # --- 停止词逻辑 ---
            for word in stop_words:
                if word in text:
                    self.log(f"检测到停止词: {word}")
                    if self.stop_on_word_var.get():
                        return False




            match = re.search(r'([A-H])', text.upper())
            if match:
                char = match.group(1)
                self.current_options_map[char] = (
                    (box[0][0] + box[2][0]) / 2 + x1,
                    (box[0][1] + box[2][1]) / 2 + y1
                )
                has_options = True

        # =========================
        # 触发文字点击逻辑（新版）
        # =========================
        trigger_word = self.trigger_word_entry.get().strip()

        if trigger_word:
            for line in result[0]:
                box, (text, conf) = line

                if trigger_word in text:
                    self.log(f"检测到触发文字：{trigger_word}")

                    try:
                        delay_ms = int(self.delay_trigger_click.get())
                    except:
                        delay_ms = 3000

                    try:
                        max_times = int(self.max_trigger_times.get())
                    except:
                        max_times = 5

                    if self.trigger_click_count >= max_times:
                        self.log("达到最大触发次数，自动停止")
                        return False

                    # 计算文字中心点（全局坐标）
                    center_x = (box[0][0] + box[2][0]) / 2 + x1
                    center_y = (box[0][1] + box[2][1]) / 2 + y1

                    while True:
                        self.log("等待触发延时...")
                        time.sleep(delay_ms / 1000.0)

                        self.human_click((center_x, center_y))
                        self.log("已点击触发文字")

                        # 再次 OCR 检查是否仍存在
                        with mss.mss() as sct:
                            shot2 = np.array(
                                sct.grab({
                                    "left": int(x1),
                                    "top": int(y1),
                                    "width": int(x2 - x1),
                                    "height": int(y2 - y1)
                                })
                            )[:, :, :3]

                        result2 = ocr.ocr(shot2, cls=False)

                        still_exists = False
                        if result2 and result2[0]:
                            for line2 in result2[0]:
                                if trigger_word in line2[1][0]:
                                    still_exists = True
                                    break

                        if not still_exists:
                            self.trigger_click_count += 1
                            self.log(f"触发完成 {self.trigger_click_count}/{max_times}")

                            # ===== 新增：完成后再等待一次延时 =====
                            self.log("触发完成后等待稳定...")
                            time.sleep(delay_ms / 1000.0)

                            return True

                    # 触发完成后继续原流程
                    break

        if not has_options: self.log("无选项，停止。"); return False

        # 2. 发送 AI
        pyperclip.copy(self.prompt_text.get("1.0", tk.END).strip() + "\n" + "\n".join(lines_text))
        if self.ai_input_pos:
            self.human_click(self.ai_input_pos)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.2)
            self.human_click(self.ai_send_pos)
            if self.user_home_pos: pyautogui.moveTo(self.user_home_pos)

        # 3. 等待回答
        try:
            wait_ms = int(self.delay_ai_wait.get())
        except:
            wait_ms = 10000
        for _ in range(int(wait_ms / 500)):
            if self.stop_requested: return False
            time.sleep(0.5)

        # 4. 读答案并点击
        answer = self.get_ai_answer_text()
        if answer:
            try:
                click_interval = int(self.delay_auto_click.get()) / 1000.0
            except:
                click_interval = 2.0
            for char in answer:
                if self.stop_requested: return False
                if char in self.current_options_map:
                    self.log(f"点击选项 {char}")
                    self.human_click(self.current_options_map[char])
                    time.sleep(click_interval)

            # 5. 点击下一题 (同步优化：两次点击，含间隔)
            self.action_next_question()
            return True
        return False

    def action_next_question(self):
        """执行下一题：点击 -> 延迟 -> 点击"""
        if not self.next_btn_pos: return
        try:
            click_interval = int(self.delay_auto_click.get()) / 1000.0  # 复用点击间隔参数
        except:
            click_interval = 2.0

        self.log("执行下一题连点...")
        self.human_click(self.next_btn_pos)  # 第一次点击
        time.sleep(click_interval)  # 等待间隔
        self.human_click(self.next_btn_pos)  # 第二次点击

        # 归位鼠标方便观察
        self.root.update()
        pyautogui.moveTo(self.btn_run.winfo_rootx() + 50, self.btn_run.winfo_rooty() + 20, duration=0.5)

    def get_ai_answer_text(self):
        if not self.answer_roi: return None
        with mss.mss() as sct:
            x1, y1, x2, y2 = self.answer_roi
            shot = np.array(sct.grab({"left": int(x1), "top": int(y1), "width": int(x2 - x1), "height": int(y2 - y1)}))[
                   :, :, :3]
        result = ocr.ocr(shot, cls=False)
        if result and result[0]:
            raw_ans = "".join([line[1][0] for line in result[0]]).upper()
            return "".join(re.findall(r'[A-H]', raw_ans))
        return None

    def stop_task(self):
        self.stop_requested = True; self.is_running = False

    def update_hotkeys(self):
        keyboard.unhook_all()
        keyboard.add_hotkey(self.hk_ocr_entry.get().strip(), self.start_automation)
        keyboard.add_hotkey(self.hk_next_entry.get().strip(), self.action_next_question)
        keyboard.add_hotkey('esc', self.stop_task)

    def start_hotkey_listener(self):
        threading.Thread(target=self.update_hotkeys, daemon=True).start()

    # --- 选择逻辑 (省略，代码同前) ---
    def select_roi(self):
        s = RegionSelector(self.root, "框选题目区"); self.root.wait_window(
            s.win); self.roi = s.selection if not s.cancelled else self.roi; return not s.cancelled

    def select_answer_roi(self):
        s = RegionSelector(self.root, "框选答案区"); self.root.wait_window(
            s.win); self.answer_roi = s.selection if not s.cancelled else self.answer_roi; return not s.cancelled

    def select_ai_input(self):
        p = PointSelector(self.root, "点击输入框"); self.root.wait_window(
            p.win); self.ai_input_pos = p.pos if not p.cancelled else self.ai_input_pos; return not p.cancelled

    def select_ai_send(self):
        p = PointSelector(self.root, "点击发送键"); self.root.wait_window(
            p.win); self.ai_send_pos = p.pos if not p.cancelled else self.ai_send_pos; return not p.cancelled

    def select_next_btn(self):
        p = PointSelector(self.root, "点击下一题点"); self.root.wait_window(
            p.win); self.next_btn_pos = p.pos if not p.cancelled else self.next_btn_pos; return not p.cancelled

    def select_home_pos(self):
        p = PointSelector(self.root, "点击归位点"); self.root.wait_window(
            p.win); self.user_home_pos = p.pos if not p.cancelled else self.user_home_pos; return not p.cancelled

    def full_init(self):
        for f in [self.select_roi, self.select_answer_roi, self.select_ai_input, self.select_ai_send,
                  self.select_next_btn, self.select_home_pos]:
            if not f(): break


if __name__ == "__main__":
    app = OCRApp()
    app.root.mainloop()
