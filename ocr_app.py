"""大扫除小助手 V3.0 主窗口。

手机端操作全部走 adb（截图 + tap），元素位置运行时 OCR 自动定位，不落盘；
外部 AI 窗口（答案区/输入框/发送键）仅运行期标定，关闭即清除；
仅 Prompt 与延时参数持久化到 params.json。
"""

import json
import os
import re
import threading
import time

import mss
import numpy as np
import pyautogui
import pyperclip
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
from tkinter import scrolledtext

import importlib
import importlib.util

from adb_service import AdbService, AdbError
from config import (
    ADB_TIMEOUT, APP_VERSION, DEFAULT_AI_WAIT_MS, DEFAULT_CLICK_RESPONSE_MS,
    DEFAULT_PROMPT, DEFAULT_QUESTION_LIMIT, DEFAULT_STEP_MS,
    MAX_CONSECUTIVE_FAILURES, MAX_LONG_Q_SCROLLS, MAX_PREVIEW_FAILURES,
    PARAMS_FILE, PREVIEW_INTERVAL, PREVIEW_WIDTH, SCROLL_SETTLE_S,
    SHOT_DIR,
    WORD_CORRECT_ANSWER, WORD_FINISH, WORD_LOADING,
    WORD_GROUP_END, WORD_NEXT, WORD_SUBMIT, ocr,
)
from gui_selectors import PointSelector, RegionSelector
from image_clean import remove_watermark
from quiz_parser import (HINT_WORD, OPTION_PREFIX_RE, QUESTION_MARKER,
                         TYPE_WORDS, parse_question_page)

pyautogui.FAILSAFE = False

STATE_QUESTION = "question"     # 答题页（检测到选项/查看提示）
STATE_SUBMITTED = "submitted"   # 已选择，出现"确定"
STATE_RESULT = "result"         # 已判分，出现"下一题"
STATE_GROUP_END = "group_end"   # 组结束，出现"再来一组"
STATE_UNKNOWN = "unknown"


class OCRApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_VERSION)
        self.root.geometry("1040x760+400+30")
        self.root.attributes("-topmost", True)

        # --- adb 与设备状态 ---
        self.adb = AdbService()
        self.adb_lock = threading.Lock()  # 预览与自动化共用一条 adb 连接，互斥访问
        self.device_serial = None
        self.device_resolution = None

        # --- 运行期标定（仅内存，关闭即清除） ---
        self.answer_roi = None       # 外部 AI 回答区（PC 屏幕坐标）
        self.ai_input_pos = None     # 外部 AI 输入框
        self.ai_send_pos = None      # 外部 AI 发送键

        # --- 任务状态 ---
        self.stop_event = threading.Event()
        self.is_running = False
        self.stats = {"answered": 0}

        # --- 预览线程 ---
        self._preview_stop = threading.Event()
        self._preview_thread = None
        self._tk_img = None  # 保持引用防止被 GC

        self._setup_ui()
        self._load_params()
        self._setup_esc_listener()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if not self.adb.adb_path:
            self.log("未找到 adb，请把 scrcpy 目录中的 adb.exe 及两个 dll "
                     "复制到项目 adb/ 目录（或加入系统 PATH）。")

    # ==================================================================
    # UI
    # ==================================================================
    def _setup_ui(self):
        # --- 左右两栏：左侧 STEP1+实时画面，右侧其余全部 ---
        left_col = tk.Frame(self.root, width=PREVIEW_WIDTH + 30)
        left_col.pack(side="left", fill="both", padx=(10, 5), pady=5)
        left_col.pack_propagate(False)  # 固定左栏宽度，预览区自行拉伸高度
        right_col = tk.Frame(self.root)
        right_col.pack(side="left", fill="both", expand=True, padx=(5, 10), pady=5)

        self._build_device_ui(left_col)
        self._build_preview_ui(left_col)
        self._build_calib_ui(right_col)
        self._build_prompt_ui(right_col)
        self._build_param_ui(right_col)
        self._build_control_ui(right_col)
        self._build_log_ui(right_col)

    def _build_device_ui(self, parent):
        # --- 设备区 ---
        dev_frame = tk.LabelFrame(parent, text=" STEP 1: ADB 设备 ",
                                  fg="#E91E63", font=("微软雅黑", 10, "bold"))
        dev_frame.pack(fill="x")

        row = tk.Frame(dev_frame)
        row.pack(fill="x", padx=5, pady=3)
        tk.Button(row, text="刷新设备", command=self.refresh_devices).pack(side="left")
        tk.Button(row, text="连接", command=self.connect_device).pack(side="right")
        self.device_combo = ttk.Combobox(row, state="readonly")
        self.device_combo.pack(side="left", fill="x", expand=True, padx=5)

        self.device_info_label = tk.Label(dev_frame, text="未连接", fg="grey",
                                          font=("微软雅黑", 9))
        self.device_info_label.pack(anchor="w", padx=8, pady=(0, 3))

    def _build_preview_ui(self, parent):
        # --- 实时预览区（拉伸填满左栏剩余空间） ---
        prev_frame = tk.LabelFrame(parent, text=" 手机实时画面 ",
                                   fg="#3F51B5", font=("微软雅黑", 10, "bold"))
        prev_frame.pack(fill="both", expand=True, pady=(5, 0))
        self.preview_canvas = tk.Canvas(prev_frame, bg="#222",
                                        highlightthickness=0)
        self.preview_canvas.pack(fill="both", expand=True, padx=5, pady=5)

    def _build_calib_ui(self, parent):
        # --- AI 窗口标定区 ---
        cal_frame = tk.LabelFrame(parent, text=" STEP 2: 外部 AI 窗口标定（仅本次运行有效） ",
                                  fg="#009688", font=("微软雅黑", 10, "bold"))
        cal_frame.pack(fill="x")
        cal_btns = tk.Frame(cal_frame)
        cal_btns.pack(pady=3)
        tk.Button(cal_btns, text="一键顺序标定", width=14, fg="#009688",
                  font=("微软雅黑", 9, "bold"),
                  command=self.calibrate_all).grid(row=0, column=0, padx=4)
        tk.Button(cal_btns, text="重新框选答案区", width=14,
                  command=self.select_answer_roi).grid(row=0, column=1, padx=4)
        tk.Button(cal_btns, text="重新点选输入框", width=14,
                  command=self.select_ai_input).grid(row=0, column=2, padx=4)
        tk.Button(cal_btns, text="重新点选发送键", width=14,
                  command=self.select_ai_send).grid(row=0, column=3, padx=4)
        self.calib_status = tk.Label(cal_frame, text="", fg="#795548",
                                     font=("微软雅黑", 9))
        self.calib_status.pack(pady=2)
        self._update_calib_status()

    def _build_prompt_ui(self, parent):
        # --- AI 指令 ---
        tk.Label(parent, text=" STEP 3: AI 指令 (Prompt) ", fg="#009688",
                 font=("微软雅黑", 10, "bold")).pack(pady=(8, 0))
        self.prompt_text = scrolledtext.ScrolledText(parent, height=3,
                                                     font=("微软雅黑", 9))
        self.prompt_text.pack(fill="x", padx=10)
        self.prompt_text.bind("<KeyRelease>", lambda e: self._save_params())

    def _build_param_ui(self, parent):
        # --- 参数区 ---
        param_frame = tk.LabelFrame(parent, text=" 延时参数（自动保存） ",
                                    fg="#795548", font=("微软雅黑", 10, "bold"))
        param_frame.pack(fill="x", pady=5)
        grid = tk.Frame(param_frame)
        grid.pack(pady=4)

        tk.Label(grid, text="AI回答等待(ms):").grid(row=0, column=0)
        self.delay_ai_wait = tk.Entry(grid, width=8, justify="center")
        self.delay_ai_wait.grid(row=0, column=1, padx=5)
        self.delay_ai_wait.bind("<FocusOut>", lambda e: self._save_params())

        tk.Label(grid, text="点击后响应等待(ms):").grid(row=0, column=2)
        self.delay_click_response = tk.Entry(grid, width=8, justify="center")
        self.delay_click_response.grid(row=0, column=3, padx=5)
        self.delay_click_response.bind("<FocusOut>", lambda e: self._save_params())

        tk.Label(grid, text="操作间微延迟(ms):").grid(row=1, column=0, pady=5)
        self.delay_step = tk.Entry(grid, width=8, justify="center")
        self.delay_step.grid(row=1, column=1)
        tk.Label(grid, text="（点击节奏缓冲）", fg="grey",
                 font=("微软雅黑", 8)).grid(row=1, column=2, columnspan=2)

        tk.Label(grid, text="答题数量上限:").grid(row=2, column=0, pady=5)
        self.question_limit = tk.Entry(grid, width=8, justify="center")
        self.question_limit.grid(row=2, column=1)
        self.question_limit.bind("<FocusOut>", lambda e: self._save_params())
        tk.Label(grid, text="（答满后自动停止）", fg="grey",
                 font=("微软雅黑", 8)).grid(row=2, column=2, columnspan=2)

        self.save_shots_var = tk.BooleanVar(value=False)
        tk.Checkbutton(grid, text="保存每题判分截图",
                       variable=self.save_shots_var,
                       command=self._save_params).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(5, 0), padx=8)
        tk.Label(grid, text="（提交后存到以启动时间命名的文件夹）", fg="grey",
                 font=("微软雅黑", 8)).grid(row=3, column=2, columnspan=2)

    def _build_control_ui(self, parent):
        # --- 彩虹提示 ---
        hint_frame = tk.Frame(parent)
        hint_frame.pack(pady=(8, 0))
        colors = ("#FF3B30", "#FF9500", "#DAA520", "#34A853",
                  "#1E90FF", "#6A5ACD", "#9932CC")
        for i, ch in enumerate("建议打开勿扰模式，防止微信等APP通知干扰。"):
            tk.Label(hint_frame, text=ch, fg=colors[i % len(colors)],
                     font=("微软雅黑", 10, "bold")).pack(side="left")
        # --- 运行控制 ---
        self.btn_run = tk.Button(parent, text="【启动全自动任务】", bg="#4CAF50",
                                 fg="white", font=("微软雅黑", 12, "bold"),
                                 height=2, command=self.start_automation)
        self.btn_run.pack(fill="x", padx=30, pady=5)
        tk.Button(parent, text="紧急停止 (Esc)", bg="#f44336", fg="white",
                  command=self.stop_task).pack(fill="x", padx=80)

    def _build_log_ui(self, parent):
        # --- 日志区（填满右栏剩余空间） ---
        tk.Label(parent, text="运行日志", fg="#3F51B5",
                 font=("微软雅黑", 10, "bold")).pack(pady=(8, 0))
        self.log_area = scrolledtext.ScrolledText(parent,
                                                  font=("Consolas", 9), bg="#f8f8f8")
        self.log_area.pack(fill="both", expand=True, padx=10, pady=5)

    # ==================================================================
    # 日志（线程安全）
    # ==================================================================
    def log(self, message):
        if threading.current_thread() is threading.main_thread():
            self._append_log(message)
        else:
            try:
                self.root.after(0, self._append_log, message)
            except tk.TclError:
                pass
        print(message)

    def _append_log(self, message):
        self.log_area.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_area.see(tk.END)

    # ==================================================================
    # 参数持久化（仅参数，元素位置一律不落盘）
    # ==================================================================
    def _load_params(self):
        params = {}
        try:
            with open(PARAMS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                params = data
        except (OSError, ValueError):
            params = {}

        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert(tk.END, params.get("prompt", DEFAULT_PROMPT))
        self.delay_ai_wait.insert(0, str(params.get("delay_ai_wait_ms",
                                                    DEFAULT_AI_WAIT_MS)))
        self.delay_click_response.insert(0, str(params.get(
            "delay_click_response_ms", DEFAULT_CLICK_RESPONSE_MS)))
        self.delay_step.insert(0, str(params.get("delay_step_ms", DEFAULT_STEP_MS)))
        self.question_limit.insert(0, str(params.get("question_limit",
                                                     DEFAULT_QUESTION_LIMIT)))
        self.save_shots_var.set(bool(params.get("save_result_screens", False)))
        self.log("参数已加载。")

    def _save_params(self):
        data = {
            "app_version": APP_VERSION,
            "prompt": self.prompt_text.get("1.0", tk.END).strip(),
            "delay_ai_wait_ms": self._int_of(self.delay_ai_wait, DEFAULT_AI_WAIT_MS),
            "delay_click_response_ms": self._int_of(self.delay_click_response,
                                                    DEFAULT_CLICK_RESPONSE_MS),
            "delay_step_ms": self._int_of(self.delay_step, DEFAULT_STEP_MS),
            "question_limit": self._int_of(self.question_limit,
                                           DEFAULT_QUESTION_LIMIT),
            "save_result_screens": bool(self.save_shots_var.get()),
        }
        try:
            with open(PARAMS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except OSError as e:
            self.log(f"参数保存失败：{e}")

    @staticmethod
    def _int_of(entry, default):
        try:
            return int(entry.get().strip())
        except ValueError:
            return default

    # ==================================================================
    # 设备管理
    # ==================================================================
    def refresh_devices(self):
        threading.Thread(target=self._refresh_devices_worker, daemon=True).start()

    def _refresh_devices_worker(self):
        try:
            devices = self.adb.list_devices()
        except AdbError as e:
            self.log(f"刷新设备失败：{e}")
            return

        if not devices:
            self.log("未发现已授权的 adb 设备，请检查 USB 连接与调试开关。")
        else:
            self.log(f"发现 {len(devices)} 台设备。")
        self.root.after(0, self._fill_device_combo, devices)

    def _fill_device_combo(self, devices):
        self._device_list = devices
        self.device_combo["values"] = [f"{s} - {m}" for s, m in devices]
        if devices:
            self.device_combo.current(0)

    def connect_device(self):
        idx = self.device_combo.current()
        devices = getattr(self, "_device_list", [])
        if idx < 0 or idx >= len(devices):
            self.log("请先刷新并选择设备。")
            return
        serial, model = devices[idx]
        threading.Thread(target=self._connect_worker,
                         args=(serial, model), daemon=True).start()

    def _connect_worker(self, serial, model):
        self.adb.serial = serial
        try:
            w, h = self.adb.get_resolution()
            density = self.adb.get_density()
        except AdbError as e:
            self.adb.serial = None
            self.log(f"连接设备失败：{e}")
            return

        self.device_serial = serial
        self.device_resolution = (w, h)
        self.log(f"已连接 {model} ({serial})，分辨率 {w}x{h}，密度 {density}。")
        # 连接后一次性准备：防锁屏 + 调低亮度
        for desc, err in self.adb.prepare_device():
            if err:
                self.log(f"设备设置失败（{desc}）：{err}")
            else:
                self.log(f"设备设置完成：{desc}")
        # 启动 scrcpy 镜像：物理屏关闭但保持唤醒
        try:
            flags = self.adb.launch_scrcpy()
            self.log(f"scrcpy 镜像已启动（{flags}）。")
        except AdbError as e:
            self.log(f"scrcpy 未启动：{e}")
        self.root.after(0, self.device_info_label.config,
                        {"text": f"已连接: {model} ({serial})  {w}x{h}  密度 {density}",
                         "fg": "#4CAF50"})
        self._start_preview()

    # ==================================================================
    # 实时预览
    # ==================================================================
    def _start_preview(self):
        if self._preview_thread and self._preview_thread.is_alive():
            return
        self._preview_stop.clear()
        self._preview_thread = threading.Thread(target=self._preview_loop,
                                                daemon=True)
        self._preview_thread.start()
        self.log("实时预览已启动。")

    def _preview_loop(self):
        failures = 0
        while not self._preview_stop.is_set():
            frame = None
            if self.adb_lock.acquire(timeout=0.5):
                try:
                    if self.adb.serial:
                        frame = self.adb.screencap()
                except AdbError as e:
                    failures += 1
                    self.log(f"预览截图失败：{e}")
                    if failures >= MAX_PREVIEW_FAILURES:
                        self.log(f"预览连续 {failures} 次截图失败，"
                                 "判定设备已断开，自动停止。")
                        self._preview_stop.set()
                        self.root.after(0, self.stop_task)
                        break
                finally:
                    self.adb_lock.release()
            if frame is not None:
                failures = 0
                try:
                    self.root.after(0, self._show_preview, frame)
                except (tk.TclError, RuntimeError):
                    return
            self._preview_stop.wait(PREVIEW_INTERVAL)

    def _show_preview(self, frame):
        img = Image.fromarray(frame)
        # 按画布实际可用区域等比缩放并居中（窗口拉伸时自动适配）
        self.preview_canvas.update_idletasks()
        cw = max(1, self.preview_canvas.winfo_width())
        ch = max(1, self.preview_canvas.winfo_height())
        scale = min(cw / img.width, ch / img.height)
        new_w = max(1, int(img.width * scale))
        new_h = max(1, int(img.height * scale))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        self._tk_img = ImageTk.PhotoImage(img)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(cw // 2, ch // 2,
                                         anchor="center", image=self._tk_img)

    # ==================================================================
    # AI 窗口标定（仅内存）
    # ==================================================================
    def _update_calib_status(self):
        roi = "已标定" if self.answer_roi else "未标定"
        inp = "已标定" if self.ai_input_pos else "未标定"
        snd = "已标定" if self.ai_send_pos else "未标定"
        self.calib_status.config(
            text=f"答案区: {roi}    输入框: {inp}    发送键: {snd}")

    def select_answer_roi(self):
        s = RegionSelector(self.root, "框选外部 AI 的回答区")
        self.root.wait_window(s.win)
        if not s.cancelled:
            self.answer_roi = s.selection
            self.log(f"AI 回答区已标定：{tuple(int(v) for v in s.selection)}")
        self._update_calib_status()
        return not s.cancelled

    def select_ai_input(self):
        p = PointSelector(self.root, "点击外部 AI 的输入框")
        self.root.wait_window(p.win)
        if not p.cancelled:
            self.ai_input_pos = p.pos
            self.log(f"AI 输入框已标定：{p.pos}")
        self._update_calib_status()
        return not p.cancelled

    def select_ai_send(self):
        p = PointSelector(self.root, "点击外部 AI 的发送键")
        self.root.wait_window(p.win)
        if not p.cancelled:
            self.ai_send_pos = p.pos
            self.log(f"AI 发送键已标定：{p.pos}")
        self._update_calib_status()
        return not p.cancelled

    def calibrate_all(self):
        """一键标定：按 答案区 → 输入框 → 发送键 顺序依次设定；
        任意一步取消则中止后续步骤（已完成的保留）。"""
        self.log("一键标定开始：① 请框选 AI 回答区")
        if not self.select_answer_roi():
            self.log("一键标定已中止。")
            return
        self.log("一键标定：② 请点击 AI 输入框")
        if not self.select_ai_input():
            self.log("一键标定已中止。")
            return
        self.log("一键标定：③ 请点击 AI 发送键")
        if not self.select_ai_send():
            self.log("一键标定已中止。")
            return
        self.log("一键标定完成，三项均已就绪。")

    # ==================================================================
    # 启停控制
    # ==================================================================
    def start_automation(self):
        if self.is_running:
            return
        if not self.device_serial:
            self.log("请先连接 adb 设备。")
            return
        if not (self.answer_roi and self.ai_input_pos and self.ai_send_pos):
            self.log("请先完成 STEP 2 的三项 AI 窗口标定。")
            return

        self.is_running = True
        self.stop_event.clear()
        self.stats = {"answered": 0}
        # 判分截图目录：以启动时间命名，本次运行的截图全部存入
        self._shot_dir = None
        if self.save_shots_var.get():
            self._shot_dir = os.path.join(SHOT_DIR,
                                          time.strftime("%Y%m%d_%H%M%S"))
            try:
                os.makedirs(self._shot_dir, exist_ok=True)
                self.log(f"判分截图将保存到：{self._shot_dir}/")
            except OSError as e:
                self._shot_dir = None
                self.log(f"截图目录创建失败，已禁用截图：{e}")
        threading.Thread(target=self._main_loop, daemon=True).start()
        self.log(">>> 自动化任务已启动。")

    def stop_task(self):
        if self.is_running or not self.stop_event.is_set():
            self.log("收到停止指令。")
        self.stop_event.set()
        self.is_running = False

    def _interruptible_sleep(self, seconds):
        """睡眠期间响应停止，返回 True 表示被要求停止。"""
        return self.stop_event.wait(seconds)

    # ==================================================================
    # 状态机主循环
    # ==================================================================
    def _main_loop(self):
        failures = 0
        try:
            while not self.stop_event.is_set():
                ok = self._one_round()
                if ok:
                    failures = 0
                else:
                    failures += 1
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        self.log(f"连续 {failures} 轮失败，自动停止。")
                        break
                limit = self._int_of(self.question_limit, DEFAULT_QUESTION_LIMIT)
                if limit > 0 and self.stats["answered"] >= limit:
                    self.log(f"已完成 {self.stats['answered']} 题，达到上限，自动停止。")
                    break
                if self._interruptible_sleep(0.5):
                    break
        except Exception as e:
            self.log(f"任务异常终止：{e}")
        finally:
            self.is_running = False
            self.log(f">>> 自动化已安全停止，本次共完成 {self.stats['answered']} 题。")

    def _one_round(self):
        """执行一轮：截图 + OCR 判定状态并处理。返回 True 表示本轮成功。"""
        if self.adb_lock.acquire(timeout=ADB_TIMEOUT):
            try:
                frame = self.adb.screencap()
            except AdbError as e:
                self.log(f"截图失败：{e}")
                return False
            finally:
                self.adb_lock.release()
        else:
            return False

        ocr_lines = self._ocr_frame(frame)
        if ocr_lines is None:
            self.log("OCR 识别失败或无内容。")
            return False

        state, target = self._detect_state(ocr_lines)

        if state == STATE_GROUP_END:
            self.log("检测到组结束页面，点击“再来一组”。")
            return self._tap_pos(target, "再来一组")

        if state == STATE_RESULT:
            correct = self._extract_correct_answer(ocr_lines)
            if correct:
                self.log(f"本题正确答案：{correct}")
            self.stats["answered"] += 1
            if getattr(self, "_shot_dir", None):
                self._save_result_shot(frame, ocr_lines)
            btn = "完成" if any(t.strip() == WORD_FINISH
                            for _, t in ocr_lines) else "下一题"
            return self._tap_pos(target, btn)

        if state == STATE_SUBMITTED:
            self.log("检测到“确定”按钮，点击提交。")
            return self._tap_pos(target, "确定")

        if state == STATE_QUESTION:
            return self._handle_question(ocr_lines)

        self.log("当前页面无法识别，请确认手机处于答题 APP。")
        return False

    def _tap_pos(self, pos, name):
        if not pos:
            self.log(f"未能定位“{name}”按钮坐标。")
            return False
        try:
            self.adb.tap(*pos)
        except AdbError as e:
            self.log(f"点击“{name}”失败：{e}")
            return False
        self.log(f"已点击“{name}” ({int(pos[0])}, {int(pos[1])})")
        return not self._interruptible_sleep(
            self._int_of(self.delay_click_response, DEFAULT_CLICK_RESPONSE_MS) / 1000.0)

    # ------------------------------------------------------------------
    # OCR 与状态判定
    # ------------------------------------------------------------------
    def _save_result_shot(self, frame, ocr_lines):
        """判分页截图：等"加载中"字样消失后保存到本次启动的截图目录。

        提交后服务器虽已返回判分，但"加载中"遮罩可能残留一小段时间，
        此时截图会带上遮罩——轮询等待其消失（最多约 5 秒）再存图。
        """
        for _ in range(10):
            if frame is None or not any(WORD_LOADING in t
                                        for _, t in ocr_lines):
                break
            if self._interruptible_sleep(0.5):
                return
            if self.adb_lock.acquire(timeout=ADB_TIMEOUT):
                try:
                    frame = self.adb.screencap()
                except AdbError:
                    frame = None
                finally:
                    self.adb_lock.release()
            if frame is not None:
                ocr_lines = self._ocr_frame(frame) or []
        if frame is None:
            self.log("判分截图失败：截图不可用。")
            return
        name = f"{self.stats['answered']:03d}_{time.strftime('%H%M%S')}.png"
        path = os.path.join(self._shot_dir, name)
        try:
            Image.fromarray(frame).save(path)
        except (OSError, ValueError) as e:
            self.log(f"判分截图保存失败：{e}")
            return
        self.log(f"判分截图已保存：{name}")

    def _ocr_frame(self, frame):
        """全屏 OCR（先抹除粉色水印），返回 [(box, text), ...]；失败返回 None。"""
        result = ocr.ocr(remove_watermark(frame), cls=False)
        if not result or not result[0]:
            return None
        return [(line[0], line[1][0]) for line in result[0]]

    def _detect_state(self, ocr_lines):
        """按优先级判定页面状态，返回 (state, 目标按钮中心坐标或 None)。"""
        for box, text in ocr_lines:
            if WORD_GROUP_END in text:
                return STATE_GROUP_END, self._box_center(box)
        for box, text in ocr_lines:
            # 判分页：“下一题”或本组最后一题的“完成”
            if WORD_NEXT in text or text.strip() == WORD_FINISH:
                return STATE_RESULT, self._box_center(box)
        for box, text in ocr_lines:
            if text.strip() == WORD_SUBMIT:
                return STATE_SUBMITTED, self._box_center(box)
        # 选项前缀或"查看提示"均视为答题页（判断题可能漏检全部字母前缀）
        if any(OPTION_PREFIX_RE.match(text) or HINT_WORD in text
               for _, text in ocr_lines):
            return STATE_QUESTION, None
        # 长题页：选项前缀可能全部被挤出屏幕，用题型标签/"题目"标记兜底
        if any(any(w in text for w in TYPE_WORDS)
               or QUESTION_MARKER in text for _, text in ocr_lines):
            return STATE_QUESTION, None
        return STATE_UNKNOWN, None

    @staticmethod
    def _box_center(box):
        cx = (box[0][0] + box[2][0]) / 2
        cy = (box[0][1] + box[2][1]) / 2
        return cx, cy

    @staticmethod
    def _extract_correct_answer(ocr_lines):
        for _, text in ocr_lines:
            if WORD_CORRECT_ANSWER in text:
                letters = re.findall(r"[A-H]", text)
                if letters:
                    return ",".join(letters)
        return None

    # ------------------------------------------------------------------
    # 答题页处理
    # ------------------------------------------------------------------
    def _handle_question(self, ocr_lines):
        frame_w, frame_h = (self.device_resolution
                            if self.device_resolution else (1440, 3200))
        question_text, option_pos = parse_question_page(ocr_lines,
                                                        frame_w, frame_h)
        if not question_text and not option_pos:
            self.log("未能解析出题干或选项，本轮跳过。")
            return False

        # 长题模式：选项不足 2 个（题干过长把选项挤出屏幕）→ 滚动采集
        long_mode = len(option_pos) < 2
        if long_mode:
            self.log(f"仅检出 {len(option_pos)} 个选项，疑似题干过长，"
                     "尝试滚动采集选项…")
            question_text, option_pos = self._collect_scrolled(
                question_text, option_pos, frame_w, frame_h)
            if not question_text or not option_pos:
                self.log("滚动后仍无法采集到题目与选项，本轮跳过。")
                return False
            self.log("滚动采集完成，选项：" +
                     str({k: v["text"][:20] for k, v in option_pos.items()}))

        self.log(f"识别题目：{question_text[:60]}...")
        self.log("选项坐标：" + str(
            {k: (int(v["pos"][0]), int(v["pos"][1]))
             for k, v in option_pos.items()}))

        # 题干 + 选项全文一起发给 AI，否则 AI 无法作答
        option_lines = "\n".join(
            f"{letter}. {info['text']}" for letter, info in option_pos.items())
        full_question = f"{question_text}\n{option_lines}"

        # 1. 发给外部 AI
        self._send_question_to_ai(full_question)

        # 2. 等待 AI 作答
        wait_ms = self._int_of(self.delay_ai_wait, DEFAULT_AI_WAIT_MS)
        self.log(f"等待 AI 回答 {wait_ms}ms ...")
        if self._interruptible_sleep(wait_ms / 1000.0):
            return False

        # 3. 读取 AI 回答
        answer = self._read_ai_answer()
        if not answer:
            self.log("未读取到有效答案，本轮跳过。")
            return False
        self.log(f"解析后答案：{answer}")

        # 4. 逐个点击选项
        interval = self._int_of(self.delay_click_response,
                                DEFAULT_CLICK_RESPONSE_MS) / 1000.0
        if long_mode:
            # 长题模式：选项可能不在当前屏幕，需滚动搜索定位
            if not self._click_answers_scrolled(answer, frame_w, frame_h):
                return False
        else:
            for ch in answer:
                if self.stop_event.is_set():
                    return False
                if ch not in option_pos:
                    self.log(f"答案字母 {ch} 未找到对应选项，跳过。")
                    continue
                try:
                    self.adb.tap(*option_pos[ch]["pos"])
                except AdbError as e:
                    self.log(f"点击选项 {ch} 失败：{e}")
                    return False
                self.log(f"已点击选项 {ch}")
                if self._interruptible_sleep(
                        self._int_of(self.delay_step, DEFAULT_STEP_MS) / 1000.0):
                    return False

        # 5. 提交：等页面出现"确定"后点击
        return self._wait_and_submit(interval)

    # ------------------------------------------------------------------
    # 长题：滚动采集与搜索点击
    # ------------------------------------------------------------------
    def _screenshot_ocr(self):
        """加锁截图 + OCR，返回 [(box, text), ...]；失败返回 None。"""
        if self.adb_lock.acquire(timeout=ADB_TIMEOUT):
            try:
                frame = self.adb.screencap()
            except AdbError as e:
                self.log(f"截图失败：{e}")
                frame = None
            finally:
                self.adb_lock.release()
        else:
            return None
        if frame is None:
            return None
        return self._ocr_frame(frame)

    def _scroll_page(self, frame_w, frame_h, reverse=False):
        """滑动答题页。默认上滑使页面下滚（露出下方内容），reverse 反向。"""
        x = frame_w / 2
        y_from = frame_h * (0.3 if reverse else 0.8)
        y_to = frame_h * (0.8 if reverse else 0.3)
        if self.adb_lock.acquire(timeout=ADB_TIMEOUT):
            try:
                self.adb.swipe(x, y_from, x, y_to, 400)
            except AdbError as e:
                self.log(f"滚动失败：{e}")
            finally:
                self.adb_lock.release()
        self._interruptible_sleep(SCROLL_SETTLE_S)

    def _collect_scrolled(self, question_text, option_pos, frame_w, frame_h):
        """长题滚动采集：逐屏下滚，合并选项与题干尾部。

        页面从顶部加载，首帧题干完整；滚动只为补齐被挤出屏幕的选项，
        顺带把极端情况下滚出屏幕底部的题干尾部拼接进来。
        """
        self._scrolled_down = 0
        for _ in range(MAX_LONG_Q_SCROLLS):
            if self.stop_event.is_set():
                break
            self._scroll_page(frame_w, frame_h)
            self._scrolled_down += 1
            lines = self._screenshot_ocr()
            if not lines:
                continue
            q_new, opts_new = parse_question_page(lines, frame_w, frame_h)
            added = 0
            for letter, info in opts_new.items():
                old = option_pos.get(letter)
                if info["text"] and (old is None or not old["text"]):
                    option_pos[letter] = info
                    added += 1
            # 题干尾部拼接（去整句重复；局部重叠允许少量冗余）
            if q_new and q_new not in question_text \
                    and question_text not in q_new:
                question_text = (question_text + " " + q_new).strip()
            if not added:
                break  # 本屏无新选项，视为已滚到底
        return question_text, option_pos

    def _find_option_on_screen(self, letter, frame_w, frame_h):
        """当前屏 OCR 找到指定字母的选项前缀框，返回点击坐标；否则 None。"""
        lines = self._screenshot_ocr()
        if not lines:
            return None
        pat = re.compile(rf'^\s*{letter}\s*[.。:：]')
        band = frame_w * 0.19
        cands = [box for box, text in lines if pat.match(text)]
        if not cands:
            return None
        # 优先竖带内的前缀框（选项字母列），否则取第一个命中
        for box in cands:
            if min(p[0] for p in box) <= band:
                return frame_w / 2, self._box_center(box)[1]
        return frame_w / 2, self._box_center(cands[0])[1]

    def _click_answers_scrolled(self, answer, frame_w, frame_h):
        """长题模式：滚动搜索每个答案字母并点击；找不到则弃题点 A。"""
        rel = getattr(self, "_scrolled_down", 0)  # 相对页面顶部的下滚屏数
        step_s = self._int_of(self.delay_step, DEFAULT_STEP_MS) / 1000.0
        for ch in answer:
            if self.stop_event.is_set():
                return False
            pos = self._find_option_on_screen(ch, frame_w, frame_h)
            while pos is None and rel > 0:
                self._scroll_page(frame_w, frame_h, reverse=True)
                rel -= 1
                pos = self._find_option_on_screen(ch, frame_w, frame_h)
            while pos is None and rel < MAX_LONG_Q_SCROLLS:
                self._scroll_page(frame_w, frame_h)
                rel += 1
                pos = self._find_option_on_screen(ch, frame_w, frame_h)
            if pos is None:
                self.log(f"滚动后仍未找到选项 {ch}，放弃本题，改点 A 提交。")
                pos = self._find_option_on_screen("A", frame_w, frame_h)
                if pos is None:
                    return False
            try:
                self.adb.tap(*pos)
            except AdbError as e:
                self.log(f"点击选项 {ch} 失败：{e}")
                return False
            self.log(f"已点击选项 {ch} ({int(pos[0])}, {int(pos[1])})")
            if self._interruptible_sleep(step_s):
                return False
        return True

    def _wait_and_submit(self, timeout_s):
        """等待"确定"按钮出现并点击，超时则兜底直点最后已知位置。"""
        deadline = time.time() + max(timeout_s, 2.0)
        while time.time() < deadline:
            if self.stop_event.is_set():
                return False
            if self.adb_lock.acquire(timeout=1.0):
                try:
                    frame = self.adb.screencap()
                except AdbError:
                    frame = None
                finally:
                    self.adb_lock.release()
                if frame is not None:
                    lines = self._ocr_frame(frame)
                    if lines:
                        state, pos = self._detect_state(lines)
                        if state == STATE_SUBMITTED and pos:
                            return self._tap_pos(pos, "确定")
                        if state == STATE_RESULT:
                            self.log("页面已直接进入判分页，跳过提交。")
                            return True
            self._interruptible_sleep(0.5)
        self.log("等待“确定”按钮超时。")
        return False

    # ------------------------------------------------------------------
    # 外部 AI 窗口交互（PC 侧，沿用剪贴板 + pyautogui）
    # ------------------------------------------------------------------
    def _send_question_to_ai(self, question_text):
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        pyperclip.copy(f"{prompt}\n{question_text}")
        pyautogui.click(*self.ai_input_pos)
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        pyautogui.click(*self.ai_send_pos)
        self.log("题目已发送给外部 AI。")

    def _read_ai_answer(self):
        """截图外部 AI 回答区并 OCR，提取 A-H 字母。"""
        if not self.answer_roi:
            return None
        x1, y1, x2, y2 = (int(v) for v in self.answer_roi)
        with mss.mss() as sct:
            shot = np.array(sct.grab({"left": x1, "top": y1,
                                      "width": x2 - x1, "height": y2 - y1}))[:, :, :3]
        result = ocr.ocr(shot, cls=False)
        if not result or not result[0]:
            return None
        raw = "".join(line[1][0] for line in result[0]).upper()
        self.log(f"AI 回答区 OCR 原文：{raw}")
        return "".join(re.findall(r"[A-H]", raw))

    # ==================================================================
    # 全局 Esc 与关闭
    # ==================================================================
    def _setup_esc_listener(self):
        self.root.bind("<Escape>", lambda e: self.stop_task())
        self._keyboard_module = None
        self._keyboard_hotkey = None
        if importlib.util.find_spec("keyboard") is None:
            self.log("未安装 keyboard 库，Esc 仅在本窗口聚焦时生效。")
            return
        self._keyboard_module = importlib.import_module("keyboard")
        try:
            self._keyboard_hotkey = self._keyboard_module.add_hotkey(
                "esc", self._on_global_esc, suppress=False)
            self.log("已启用全局 Esc 监听（不拦截按键）。")
        except Exception as e:
            self._keyboard_module = None
            self._keyboard_hotkey = None
            self.log(f"全局 Esc 监听启用失败：{e}")

    def _on_global_esc(self):
        if self.is_running:
            self.log("检测到全局 Esc，执行紧急停止。")
            self.stop_task()

    def on_close(self):
        self.stop_event.set()
        self._preview_stop.set()
        if self._preview_thread and self._preview_thread.is_alive():
            self._preview_thread.join(timeout=2)
        if self._keyboard_module and self._keyboard_hotkey:
            try:
                self._keyboard_module.remove_hotkey(self._keyboard_hotkey)
            except Exception:
                pass
        self.root.destroy()
