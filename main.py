import tkinter as tk
from tkinter import scrolledtext
import time
import numpy as np
import mss
import mss.tools
from datetime import datetime
import pyperclip
import pyautogui
import threading
import re
from config import ocr
import json
import os
from tkinter import messagebox
from gui_selectors import RegionSelector, PointSelector

# --- 全局常量 ---
DEFAULT_PROMPT = "请给出这道题的答案。如果单选，给出一个大写字母选项；如果多选，选项间用逗号隔开：\n"
CONFIG_FILE = "positions.json"
APP_VERSION ="大扫除小助手 V2.3"

class OCRApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_VERSION)
        self.root.geometry("520x850+800+50")
        self.root.attributes("-topmost", True)

        self.roi = None
        self.answer_roi = None
        self.ai_input_pos = None
        self.ai_send_pos = None
        self.next_btn_pos = None

        self.is_running = False
        self.stop_requested = False
        self.current_options_map = {}
        self.config_loaded = False

        self.setup_ui()
        self.load_config()
        self.root.bind('<Escape>', lambda e: self.stop_task())

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

        tk.Label(self.root, text="自动检测下一题按钮文字:", font=("微软雅黑", 9)).pack()
        self.confirm_word_entry = tk.Entry(self.root, width=30, justify='center')
        self.confirm_word_entry.insert(0, "确定")
        self.confirm_word_entry.pack(pady=2)

        tk.Label(self.root, text="发送AI前，严格去掉末尾文本:", font=("微软雅黑", 9)).pack()
        self.trim_suffix_entry = tk.Entry(self.root, width=30, justify='center')
        self.trim_suffix_entry.insert(0, "查看提示")
        self.trim_suffix_entry.pack(pady=2)

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

        self.btn_run = tk.Button(self.root, text="【启动全自动任务 】", bg="#4CAF50", fg="white",
                                 font=("微软雅黑", 12, "bold"), height=2, command=self.start_automation)
        self.btn_run.pack(fill="x", padx=40, pady=5)
        tk.Button(self.root, text="紧急停止 (Esc)", bg="#f44336", fg="white", command=self.stop_task).pack(fill="x",
                                                                                                           padx=100)

        self.log_area = scrolledtext.ScrolledText(self.root, height=10, font=("Consolas", 9), bg="#f8f8f8")
        self.log_area.pack(fill="x", padx=20, pady=5)

        fix_frame = tk.Frame(self.root);
        fix_frame.pack(pady=5)
        btns = [("题目区", self.select_roi), ("答案区", self.select_answer_roi),
                ("输入框", self.select_ai_input), ("发送键", self.select_ai_send)]
        for i, (name, cmd) in enumerate(btns):
            tk.Button(fix_frame, text=name, width=10, command=cmd).grid(row=0, column=i, padx=4, pady=2)

    def _get_click_interval_s(self):
        try:
            return int(self.delay_auto_click.get()) / 1000.0
        except:
            return 2.0

    def _trim_question_suffix(self, question_text):
        suffix = self.trim_suffix_entry.get().strip()
        if suffix and question_text.endswith(suffix):
            self.log(f"检测到题目尾部“{suffix}”，已严格移除。")
            return question_text[:-len(suffix)]
        return question_text

    def detect_and_cache_confirm_btn(self):
        """在题目区域 OCR 查找“确定”等目标文字，找到后写入 next_btn_pos 并持久化。"""
        if not self.roi:
            return False

        target = self.confirm_word_entry.get().strip() or "确定"
        x1, y1, x2, y2 = self.roi
        with mss.mss() as sct:
            shot = np.array(
                sct.grab({
                    "left": int(x1),
                    "top": int(y1),
                    "width": int(x2 - x1),
                    "height": int(y2 - y1)
                })
            )[:, :, :3]

        result = ocr.ocr(shot, cls=False)
        if not result or not result[0]:
            self.log(f"OCR 未识别到“{target}”。")
            return False

        best = None
        for line in result[0]:
            box, (text, conf) = line
            t = text.strip()
            if t == target:
                best = (box, text, conf)
                break
            if target in t and best is None:
                best = (box, text, conf)

        if not best:
            self.log(f"未找到目标按钮文字：{target}")
            return False

        box, text, conf = best
        center_x = (box[0][0] + box[2][0]) / 2 + x1
        center_y = (box[0][1] + box[2][1]) / 2 + y1
        self.next_btn_pos = (center_x, center_y)
        self.save_config(changed_key="next_btn_pos")
        self.log(f"已自动定位“{target}”并写入配置: ({int(center_x)}, {int(center_y)})")
        return True

    def build_option_map_from_layout(self, ocr_lines, x1, y1, x2, y2):
        """根据OCR得到的题干/选项布局，模拟选项点击位置（严格限制在题目ROI内）。"""
        option_map = {}

        question_bottom = y1
        option_candidates = []  # (letter, center_y, center_x)

        for box, text in ocr_lines:
            center_x = (box[0][0] + box[2][0]) / 2 + x1
            center_y = (box[0][1] + box[2][1]) / 2 + y1
            box_bottom = max(p[1] for p in box) + y1

            m = re.match(r'^\s*([A-H])[\.。:：、\)）]?\s*', text.upper())
            if m:
                option_candidates.append((m.group(1), center_y, center_x))
            else:
                # 仅把非选项文本当作题干，用于估算选项起始位置
                question_bottom = max(question_bottom, box_bottom)

        # 若识别到真实选项字母，直接使用其纵向分布
        for ch, cy, _ in sorted(option_candidates, key=lambda t: t[1]):
            if ch not in option_map:
                option_map[ch] = cy

        sorted_y = sorted([cy for _, cy, _ in option_candidates])
        if len(sorted_y) >= 2:
            gaps = [sorted_y[i + 1] - sorted_y[i] for i in range(len(sorted_y) - 1)]
            step = max(36.0, min(95.0, float(np.median(gaps))))
            start_y = sorted_y[0]
        else:
            # 没有或仅有一行时，用题干底部到roi底部估算4行
            option_top = min(max(question_bottom + 10, y1 + 40), y2 - 120)
            available_h = max(120.0, y2 - option_top - 20)
            step = max(42.0, min(88.0, available_h / 4.0))
            start_y = option_top + step * 0.5

        # 至少补齐A-D，最多补到H
        for idx in range(8):
            ch = chr(ord('A') + idx)
            if ch not in option_map:
                option_map[ch] = start_y + idx * step

        # X 使用“识别到的选项字母X中位数”，否则使用ROI内固定安全列
        if option_candidates:
            xs = sorted([cx for _, _, cx in option_candidates])
            click_x = xs[len(xs) // 2] + 120
        else:
            click_x = x1 + (x2 - x1) * 0.6
        click_x = min(max(click_x, x1 + 20), x2 - 20)

        final_map = {}
        for ch, cy in option_map.items():
            safe_y = min(max(cy, y1 + 20), y2 - 20)
            final_map[ch] = (click_x, safe_y)

        return final_map

    # --- 拟人化点击工具 ---
    def human_click(self, pos):
        """执行 移动 -> 停顿 -> 点击 -> 停顿 的流程"""
        try:
            step_delay = int(self.delay_step.get()) / 1000.0
        except:
            step_delay = 0.3

        self.log(f"移动到: ({int(pos[0])}, {int(pos[1])})")

        pyautogui.moveTo(pos[0], pos[1], duration=step_delay)
        time.sleep(step_delay)

        self.log("鼠标按下")
        pyautogui.mouseDown()
        time.sleep(0.05)

        self.log("鼠标抬起")
        pyautogui.mouseUp()
        time.sleep(step_delay)

    # --- 核心流程 ---
    def start_automation(self):
        if self.config_loaded:
            confirm = messagebox.askokcancel(
                "加载历史配置",
                "检测到上次保存的坐标配置。\n\n"
                "请确认当前刷题窗口位置与上次一致。\n"
                "如果窗口发生移动，请重新进行坐标初始化。\n\n"
                "是否继续启动？"
            )
            if not confirm:
                self.log("用户取消启动。")
                return
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

        ocr_lines = []
        for line in result[0]:
            box, (text, conf) = line
            lines_text.append(text)
            ocr_lines.append((box, text))

            # --- 停止词逻辑 ---
            for word in stop_words:
                if word in text:
                    self.log(f"检测到停止词: {word}")
                    if self.stop_on_word_var.get():
                        return False

        # 根据OCR布局模拟选项位置，避免点到题目外
        self.current_options_map = self.build_option_map_from_layout(ocr_lines, x1, y1, x2, y2)
        has_options = len(self.current_options_map) > 0

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
        question_text = "\n".join(lines_text)
        question_text = self._trim_question_suffix(question_text)
        pyperclip.copy(self.prompt_text.get("1.0", tk.END).strip() + "\n" + question_text)
        if self.ai_input_pos:
            self.human_click(self.ai_input_pos)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.2)
            self.human_click(self.ai_send_pos)

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
            click_interval = self._get_click_interval_s()
            for char in answer:
                if self.stop_requested: return False
                if char in self.current_options_map:
                    self.log(f"点击选项 {char}")
                    self.human_click(self.current_options_map[char])
                    time.sleep(click_interval)

            # 5. 点击下一题 (同步优化：两次点击，含间隔)
            if not self.next_btn_pos:
                self.log("未配置“下一题”按钮，开始自动识别...")
                time.sleep(click_interval)
                if not self.detect_and_cache_confirm_btn():
                    self.log("自动识别下一题按钮失败，本轮停止。")
                    return False
                time.sleep(click_interval)
            self.action_next_question()
            return True
        return False

    def action_next_question(self):
        """执行下一题：点击 -> 延迟 -> 点击"""
        if not self.next_btn_pos: return
        click_interval = self._get_click_interval_s()

        self.log("执行下一题连点...")
        # 第一次点击（提交答案）
        self.human_click(self.next_btn_pos)
        # ⭐ 截图
        self.save_question_screenshot()
        time.sleep(click_interval)
        # 第二次点击（真正翻页）
        self.human_click(self.next_btn_pos)

    def get_ai_answer_text(self):
        if not self.answer_roi: return None
        with mss.mss() as sct:
            x1, y1, x2, y2 = self.answer_roi
            shot = np.array(sct.grab({"left": int(x1), "top": int(y1), "width": int(x2 - x1), "height": int(y2 - y1)}))[
                   :, :, :3]
        result = ocr.ocr(shot, cls=False)
        if result and result[0]:
            raw_ans = "".join([line[1][0] for line in result[0]]).upper()
            self.log(f"答案区OCR原文: {raw_ans}")
            parsed = "".join(re.findall(r'[A-H]', raw_ans))
            self.log(f"解析后答案: {parsed}")
            return parsed
        return None

    def stop_task(self):
        self.stop_requested = True; self.is_running = False

    def save_config(self, changed_key=None):
        data = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    if isinstance(old_data, dict):
                        data = old_data
            except:
                data = {}

        data["app_version"] = APP_VERSION

        if changed_key is None:
            data.update({
                "roi": self.roi,
                "answer_roi": self.answer_roi,
                "ai_input_pos": self.ai_input_pos,
                "ai_send_pos": self.ai_send_pos,
                "next_btn_pos": self.next_btn_pos
            })
        else:
            data[changed_key] = getattr(self, changed_key, None)

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        if changed_key:
            self.log(f"配置已更新：{changed_key}")
        else:
            self.log("坐标配置已保存。")

    def validate_config(self, data):
        # 检查版本号
        if data.get("app_version") != APP_VERSION:
            self.log("配置版本不匹配，已忽略旧配置。")
            return False
        try:
            required_keys = [
                "roi",
                "answer_roi",
                "ai_input_pos",
                "ai_send_pos"
            ]

            for key in required_keys:
                if key not in data:
                    return False

            # 检查 roi
            if not isinstance(data["roi"], list) or len(data["roi"]) != 4:
                return False

            if not isinstance(data["answer_roi"], list) or len(data["answer_roi"]) != 4:
                return False

            # 检查点位
            for key in ["ai_input_pos", "ai_send_pos"]:
                if not isinstance(data[key], list) or len(data[key]) != 2:
                    return False

            if "next_btn_pos" in data and data["next_btn_pos"] is not None:
                if not isinstance(data["next_btn_pos"], list) or len(data["next_btn_pos"]) != 2:
                    return False

            return True

        except:
            return False

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not self.validate_config(data):
                self.log("检测到配置文件，但内容无效，已忽略。")
                return

            self.roi = tuple(data["roi"])
            self.answer_roi = tuple(data["answer_roi"])
            self.ai_input_pos = tuple(data["ai_input_pos"])
            self.ai_send_pos = tuple(data["ai_send_pos"])
            next_btn_pos = data.get("next_btn_pos")
            self.next_btn_pos = tuple(next_btn_pos) if isinstance(next_btn_pos, list) and len(next_btn_pos) == 2 else None

            self.config_loaded = True
            self.log("成功加载历史坐标配置。")

        except Exception as e:
            self.log(f"加载配置失败: {e}")

    # --- 选择逻辑 (省略，代码同前) ---
    def select_roi(self):
        s = RegionSelector(self.root, "框选题目区")
        self.root.wait_window(s.win)

        if not s.cancelled:
            self.roi = s.selection
            self.save_config(changed_key="roi")

        return not s.cancelled

    def select_answer_roi(self):
        s = RegionSelector(self.root, "框选答案区")
        self.root.wait_window(s.win)

        if not s.cancelled:
            self.answer_roi = s.selection
            self.save_config(changed_key="answer_roi")

        return not s.cancelled

    def select_ai_input(self):
        p = PointSelector(self.root, "点击输入框")
        self.root.wait_window(p.win)

        if not p.cancelled:
            self.ai_input_pos = p.pos
            self.save_config(changed_key="ai_input_pos")

        return not p.cancelled

    def select_ai_send(self):
        p = PointSelector(self.root, "点击发送键")
        self.root.wait_window(p.win)

        if not p.cancelled:
            self.ai_send_pos = p.pos
            self.save_config(changed_key="ai_send_pos")

        return not p.cancelled

    def full_init(self):
        for f in [self.select_roi, self.select_answer_roi, self.select_ai_input, self.select_ai_send]:
            if not f(): break

    def save_question_screenshot(self):
        if not self.roi:
            return

        x1, y1, x2, y2 = self.roi

        # 生成文件夹名（YYYYMM）
        now = datetime.now()
        folder_name = now.strftime("%Y%m")

        if not os.path.exists(folder_name):
            os.makedirs(folder_name)

        # 文件名（YYYYMMDD_HHMMSS.png）
        filename = now.strftime("%Y%m%d_%H%M%S.png")
        file_path = os.path.join(folder_name, filename)

        with mss.mss() as sct:
            shot = sct.grab({
                "left": int(x1),
                "top": int(y1),
                "width": int(x2 - x1),
                "height": int(y2 - y1)
            })

            mss.tools.to_png(shot.rgb, shot.size, output=file_path)

        self.log(f"题目截图已保存: {file_path}")


if __name__ == "__main__":
    app = OCRApp()
    app.root.mainloop()