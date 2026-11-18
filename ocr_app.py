"""大扫除小助手 V3.3 主窗口。

手机端操作全部走 adb（截图 + tap），元素位置运行时 OCR 自动定位，不落盘；
外部 AI 窗口（AI 页面区/输入框/发送键）仅运行期标定，关闭即清除；
仅 Prompt 与延时参数持久化到 params.json。
"""

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler

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
    ADB_TIMEOUT, AI_ANSWER_POLL_S, AI_ANSWER_RETRY_S,
    AI_SCROLL_CLICKS, AI_SCROLL_UNITS,
    APP_VERSION, BUTTON_TOP_RATIO, DEFAULT_AI_WAIT_MS,
    DEFAULT_CLICK_RESPONSE_MS,
    DEFAULT_PROMPT, DEFAULT_QUESTION_LIMIT, DEFAULT_SCROLL_SETTLE_MS,
    DEFAULT_STEP_MS, LOG_DIR,
    MAX_CONSECUTIVE_FAILURES, MAX_LONG_Q_SCROLLS, MAX_PREVIEW_FAILURES,
    PARAMS_FILE, PREVIEW_INTERVAL, PREVIEW_INTERVAL_RUNNING, PREVIEW_WIDTH,
    SCROLL_SETTLE_S, SHOT_DIR,
    WORD_CORRECT_ANSWER, WORD_FINISH, WORD_LOADING,
    WORD_GROUP_END, WORD_NEXT, WORD_SUBMIT,
)
from gui_selectors import PointSelector, RegionSelector
from image_clean import remove_watermark
from ocr_engine import (get_ocr, is_ocr_ready, ocr_error, wait_ocr_ready,
                        warm_up_ocr)
from quiz_parser import (HINT_WORD, OPTION_BAND_RATIO, OPTION_PREFIX_RE,
                         QUESTION_MARKER, TYPE_WORDS, extract_slots_from_band,
                         parse_question_page)

pyautogui.FAILSAFE = False

PARAMS_SAVE_DEBOUNCE_MS = 800   # 参数改动合并写入的延迟（毫秒）

STATE_QUESTION = "question"     # 答题页（检测到选项/查看提示）
STATE_SUBMITTED = "submitted"   # 已选择，出现"确定"
STATE_RESULT = "result"         # 已判分，出现"下一题"
STATE_GROUP_END = "group_end"   # 组结束，出现"再来一组"
STATE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class RunConfig:
    """一次自动化运行的参数快照。

    工作线程全程只读此对象，不再访问 Tk 控件（Entry / Text / BooleanVar
    均非线程安全）；运行期间在界面上的改动要到下次启动才生效。
    """

    prompt: str
    ai_wait_ms: int
    click_response_ms: int
    step_ms: int
    scroll_settle_ms: int
    question_limit: int
    save_shots: bool
    auto_scroll: bool


# AI 全页面提纯答案用的行级正则
AI_PAGE_OPTION_RE = re.compile(r"^\s*[A-H]\s*[.。:：]")            # "A. xxx" 选项行
AI_PAGE_LETTER_RE = re.compile(                                    # 独立答案字母行："A"、"A,C"
    r"^\s*([A-H](?:\s*[,，、]\s*[A-H])*)\s*[.。]?\s*$")
AI_PAGE_ANSWER_WORD_RE = re.compile(                               # "答案：A" 行
    r"答案\s*[:：]?\s*([A-H](?:\s*[,，、]\s*[A-H])*)")

_logger = logging.getLogger("spring_cleaning_helper")


def _setup_file_logging():
    """日志同时写入 logs/app-YYYYMMDD.log（按大小滚动）。

    UI 日志区随运行越来越长、重启即丢，排障（尤其是无人值守跑一整晚
    后中断）只能靠文件日志。
    """
    if _logger.handlers:            # 已初始化
        return _logger
    _logger.setLevel(logging.INFO)
    # 不向 root 传播：否则 PaddleOCR 等库配置的 root handler 会把每条
    # 日志再往控制台打一遍（带一份重复的格式），干扰输出
    _logger.propagate = False
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(LOG_DIR, time.strftime("app-%Y%m%d.log")),
            maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    except OSError:
        return _logger                # 无法建日志目录时静默降级到仅 UI
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    _logger.addHandler(handler)
    return _logger


class OCRApp:
    def __init__(self):
        self._logger = _setup_file_logging()
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
        self.ai_page_roi = None      # 整个外部 AI 对话页面（PC 屏幕坐标）
        self.ai_input_pos = None     # 外部 AI 输入框
        self.ai_send_pos = None      # 外部 AI 发送键
        self.scroll_pos = None       # AI 对话区滚轮滚动位置

        # --- 任务状态 ---
        self.stop_event = threading.Event()
        self.is_running = False
        self.stats = {"answered": 0}
        self._worker = None          # 当前自动化线程，用于识别停止/启动竞态
        self.cfg = None              # 本次运行的参数快照（RunConfig，工作线程只读）
        self._shot_dir = None        # 本次运行的判分截图目录（None 表示不截图）
        self._last_result_fp = None  # 上一张判分页指纹，避免同一页重复计数
        self._answer_ctx = None      # 同一题的 AI 答案与已点击字母（防重复点击）
        self._last_answer = None     # 上一题我方作答，用于与正确答案对比打日志
        self._scroll_index = {}      # 长题：字母 -> 相对页面顶部的下滚屏数
        self._scrolled_down = 0      # 长题：当前已下滚的屏数
        self._last_state = None      # 上一次页面状态，仅在变化时打日志
        self._save_job = None        # 参数延迟写入的 after 句柄（去抖用）
        self._ocr_warm_thread = None  # OCR 模型预热线程（后台加载，不阻塞窗口）

        # --- 预览线程 ---
        self._preview_stop = threading.Event()
        self._preview_thread = None
        self._tk_img = None        # 保持引用防止被 GC
        self._preview_size = None  # (原图宽, 原图高, 画布宽, 画布高) 缓存
        self._preview_scaled = None  # 与 _preview_size 对应的缩放后尺寸

        self._setup_ui()
        self._load_params()
        self._setup_esc_listener()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._start_ocr_warmup()

        if not self.adb.adb_path:
            self.log("未找到 adb，请把 scrcpy 目录中的 adb.exe 及两个 dll "
                     "复制到项目 adb/ 目录（或加入系统 PATH）。")

    # ==================================================================
    # OCR 引擎预热（窗口先出现，模型在后台加载）
    # ==================================================================
    def _start_ocr_warmup(self):
        """后台预热 OCR 模型。

        懒加载后模型不再拖住程序启动，但如果不预热，这 20~30 秒会挤到
        第一题里（用户看到的是“点了启动没反应”），所以启动即预热。
        """
        if self._ocr_warm_thread and self._ocr_warm_thread.is_alive():
            return
        self._ocr_warm_thread = threading.Thread(
            target=self._warm_up_ocr_worker, daemon=True, name="ocr-warmup")
        self._ocr_warm_thread.start()

    def _warm_up_ocr_worker(self):
        self.log("OCR 引擎预热开始（首次加载模型，可能需要数十秒，"
                 "期间窗口可正常操作）…")
        ok, seconds, err = warm_up_ocr()
        if ok:
            self.log(f"OCR 引擎预热完成，耗时 {seconds:.1f}s。")
        else:
            self.log(f"OCR 引擎预热失败（{seconds:.1f}s）：{err}")

    def _wait_ocr_ready(self):
        """等预热结束；返回 False 表示不可用或被要求停止。"""
        if is_ocr_ready():
            return True
        if ocr_error() is not None:
            self.log(f"OCR 引擎不可用，无法开始识别：{ocr_error()}")
            return False
        self.log("OCR 引擎尚未预热完成，任务将在预热结束后自动开始…")
        while not self.stop_event.is_set():
            wait_ocr_ready(0.2)
            if is_ocr_ready():
                self.log("OCR 引擎已就绪，开始识别。")
                return True
            if ocr_error() is not None:
                self.log(f"OCR 引擎预热失败，无法继续：{ocr_error()}")
                return False
        return False

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
        tk.Button(cal_btns, text="重新框选AI页面", width=14,
                  command=self.select_ai_page).grid(row=0, column=1, padx=4)
        tk.Button(cal_btns, text="重新点选输入框", width=14,
                  command=self.select_ai_input).grid(row=0, column=2, padx=4)
        tk.Button(cal_btns, text="重新点选发送键", width=14,
                  command=self.select_ai_send).grid(row=0, column=3, padx=4)
        # 自动滚动到页末：部分 AI 回答后不会自动滚到页底，需滚轮翻到页末才能露出答案
        scroll_row = tk.Frame(cal_frame)
        scroll_row.pack(pady=(0, 3))
        self.auto_scroll_var = tk.BooleanVar(value=False)
        tk.Checkbutton(scroll_row, text="自动滚动到页末",
                       variable=self.auto_scroll_var,
                       command=self._save_params).pack(side="left", padx=4)
        tk.Button(scroll_row, text="设定滚动位置", width=12,
                  command=self.select_scroll_pos).pack(side="left", padx=4)
        tk.Label(scroll_row, fg="grey", font=("微软雅黑", 8),
                 text="（勾选后：等 AI 作答完毕，鼠标移至此位置向下滚动，再读取答案）"
                 ).pack(side="left", padx=4)
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

        tk.Label(grid, text="滚动后延时(ms):").grid(row=4, column=0, pady=5)
        self.delay_scroll_settle = tk.Entry(grid, width=8, justify="center")
        self.delay_scroll_settle.grid(row=4, column=1)
        self.delay_scroll_settle.bind("<FocusOut>", lambda e: self._save_params())
        tk.Label(grid, text="（自动滚动全部结束后，等待此时长再 OCR 识别答案）", fg="grey",
                 font=("微软雅黑", 8)).grid(row=4, column=2, columnspan=2)

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
        self._logger.info(message)
        if threading.current_thread() is threading.main_thread():
            self._append_log(message)
        else:
            try:
                self.root.after(0, self._append_log, message)
            except (tk.TclError, RuntimeError):
                # 窗口已销毁，或主循环未运行（如无 GUI 的脚本直接构造窗口）
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
        self.delay_scroll_settle.insert(0, str(params.get(
            "delay_scroll_settle_ms", DEFAULT_SCROLL_SETTLE_MS)))
        self.question_limit.insert(0, str(params.get("question_limit",
                                                     DEFAULT_QUESTION_LIMIT)))
        self.save_shots_var.set(bool(params.get("save_result_screens", False)))
        self.auto_scroll_var.set(bool(params.get("auto_scroll_bottom", False)))
        self.log("参数已加载。")

    def _save_params(self):
        """调度一次延迟写入：连续输入时合并，避免每敲一键就落盘。"""
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except tk.TclError:
                pass
        self._save_job = self.root.after(PARAMS_SAVE_DEBOUNCE_MS,
                                        self._write_params)

    def _flush_params(self):
        """立即落盘（关闭窗口前调用，避免刚改完就关丢参数）。"""
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except tk.TclError:
                pass
            self._save_job = None
        self._write_params()

    def _write_params(self):
        """原子写入 params.json：先写临时文件再替换，避免留下半截 JSON。"""
        self._save_job = None
        data = {
            "app_version": APP_VERSION,
            "prompt": self.prompt_text.get("1.0", tk.END).strip(),
            "delay_ai_wait_ms": self._int_of(self.delay_ai_wait, DEFAULT_AI_WAIT_MS),
            "delay_click_response_ms": self._int_of(self.delay_click_response,
                                                    DEFAULT_CLICK_RESPONSE_MS),
            "delay_step_ms": self._int_of(self.delay_step, DEFAULT_STEP_MS),
            "delay_scroll_settle_ms": self._int_of(self.delay_scroll_settle,
                                                   DEFAULT_SCROLL_SETTLE_MS),
            "question_limit": self._int_of(self.question_limit,
                                           DEFAULT_QUESTION_LIMIT),
            "save_result_screens": bool(self.save_shots_var.get()),
            "auto_scroll_bottom": bool(self.auto_scroll_var.get()),
        }
        tmp_file = PARAMS_FILE + ".tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            os.replace(tmp_file, PARAMS_FILE)
        except OSError as e:
            self.log(f"参数保存失败：{e}")
            try:
                os.remove(tmp_file)
            except OSError:
                pass

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
            # 自动化运行期间降低取帧频率：adb 截图是串行资源，预览每次抢锁
            # 都会直接摊在单题耗时上（OCR 与长题滑动期间尤为明显）
            interval = (PREVIEW_INTERVAL_RUNNING if self.is_running
                        else PREVIEW_INTERVAL)
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
            self._preview_stop.wait(interval)

    def _show_preview(self, frame):
        # 按画布实际可用区域等比缩放并居中（窗口拉伸时自动适配）
        cw = self.preview_canvas.winfo_width()
        ch = self.preview_canvas.winfo_height()
        if cw <= 1 or ch <= 1:      # 窗口刚建立时尺寸未定，重新测一次
            self.preview_canvas.update_idletasks()
            cw = self.preview_canvas.winfo_width()
            ch = self.preview_canvas.winfo_height()
        cw, ch = max(1, cw), max(1, ch)
        img_h, img_w = frame.shape[:2]
        # 只在原图/画布尺寸变化时重算缩放尺寸，不每帧重复算
        size_key = (img_w, img_h, cw, ch)
        if size_key != self._preview_size:
            scale = min(cw / img_w, ch / img_h)
            self._preview_scaled = (max(1, int(img_w * scale)),
                                    max(1, int(img_h * scale)))
            self._preview_size = size_key
        # 预览只用于人眼观察，BILINEAR 足够且比 LANCZOS 快很多
        img = Image.fromarray(frame).resize(self._preview_scaled,
                                           Image.BILINEAR)
        self._tk_img = ImageTk.PhotoImage(img)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(cw // 2, ch // 2,
                                         anchor="center", image=self._tk_img)

    # ==================================================================
    # AI 窗口标定（仅内存）
    # ==================================================================
    def _update_calib_status(self):
        roi = "已标定" if self.ai_page_roi else "未标定"
        inp = "已标定" if self.ai_input_pos else "未标定"
        snd = "已标定" if self.ai_send_pos else "未标定"
        scr = "已标定" if self.scroll_pos else "未标定"
        self.calib_status.config(
            text=f"AI页面: {roi}    输入框: {inp}    发送键: {snd}"
                 f"    滚动位置: {scr}")

    def select_ai_page(self):
        s = RegionSelector(self.root,
                           "框选整个外部 AI 对话页面（含题目气泡与回答区域）")
        self.root.wait_window(s.win)
        if not s.cancelled:
            self.ai_page_roi = s.selection
            self.log(f"AI 页面区已标定：{tuple(int(v) for v in s.selection)}")
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

    def select_scroll_pos(self):
        p = PointSelector(self.root, "点击 AI 对话区中执行滚轮滚动的位置")
        self.root.wait_window(p.win)
        if not p.cancelled:
            self.scroll_pos = p.pos
            self.log(f"AI 对话区滚动位置已标定：{p.pos}")
        self._update_calib_status()
        return not p.cancelled

    def calibrate_all(self):
        """一键标定：按 AI 页面区 → 输入框 → 发送键 顺序依次设定；
        任意一步取消则中止后续步骤（已完成的保留）。"""
        self.log("一键标定开始：① 请框选整个 AI 对话页面")
        if not self.select_ai_page():
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
        # 滚动位置仅在勾选“自动滚动到页末”时才纳入一键标定
        if self.auto_scroll_var.get():
            self.log("一键标定：④ 请点击 AI 对话区的滚动位置")
            if not self.select_scroll_pos():
                self.log("一键标定已中止。")
                return
            self.log("一键标定完成，四项均已就绪。")
        else:
            self.log("一键标定完成，三项均已就绪。")

    # ==================================================================
    # 启停控制
    # ==================================================================
    def _snapshot_params(self):
        """在主线程把界面参数固化成 RunConfig，供工作线程只读使用。"""
        return RunConfig(
            prompt=self.prompt_text.get("1.0", tk.END).strip(),
            ai_wait_ms=self._int_of(self.delay_ai_wait, DEFAULT_AI_WAIT_MS),
            click_response_ms=self._int_of(self.delay_click_response,
                                           DEFAULT_CLICK_RESPONSE_MS),
            step_ms=self._int_of(self.delay_step, DEFAULT_STEP_MS),
            scroll_settle_ms=self._int_of(self.delay_scroll_settle,
                                          DEFAULT_SCROLL_SETTLE_MS),
            question_limit=self._int_of(self.question_limit,
                                        DEFAULT_QUESTION_LIMIT),
            save_shots=bool(self.save_shots_var.get()),
            auto_scroll=bool(self.auto_scroll_var.get()),
        )

    def start_automation(self):
        # 上一轮的线程可能仍在收尾：停止信号只让循环尽快退出，线程本身要
        # 跑到 finally 才结束。此刻放新线程进来，两个循环会同时操作设备，
        # 且新循环会重建停止信号，使旧线程的停止请求失效。
        if self._worker is not None and self._worker.is_alive():
            self.log("上一轮任务尚未完全退出，请稍候再启动。")
            return
        if not self.device_serial:
            self.log("请先连接 adb 设备。")
            return
        if not (self.ai_page_roi and self.ai_input_pos and self.ai_send_pos):
            self.log("请先完成 STEP 2 的三项 AI 窗口标定。")
            return
        if self.auto_scroll_var.get() and not self.scroll_pos:
            self.log("已勾选“自动滚动到页末”，请先点“设定滚动位置”，"
                     "或取消勾选。")
            return

        if not is_ocr_ready():
            self.log("OCR 引擎仍在后台预热，任务会等预热完成后自动开始识别。")

        # 参数快照必须在主线程采集（工作线程不得读取 Tk 控件）
        self.cfg = self._snapshot_params()
        self.is_running = True
        self.stop_event = threading.Event()  # 每次运行独立的停止信号
        self.stats = {"answered": 0}
        self._last_result_fp = None
        self._answer_ctx = None     # 新一轮不复用上一轮的答案/已点击集合
        self._last_answer = None
        self._scroll_index = {}
        self._scrolled_down = 0
        self._last_state = None
        # 判分截图目录：以启动时间命名，本次运行的截图全部存入
        self._shot_dir = None
        if self.cfg.save_shots:
            self._shot_dir = os.path.join(SHOT_DIR,
                                          time.strftime("%Y%m%d_%H%M%S"))
            try:
                os.makedirs(self._shot_dir, exist_ok=True)
                self.log(f"判分截图将保存到：{self._shot_dir}/")
            except OSError as e:
                self._shot_dir = None
                self.log(f"截图目录创建失败，已禁用截图：{e}")
        self._worker = threading.Thread(target=self._main_loop, daemon=True)
        self._worker.start()
        self.log(">>> 自动化任务已启动。")

    def stop_task(self):
        # 只发停止信号，不复位 is_running：改由 _main_loop 的 finally 复位，
        # 否则线程还在跑时界面就判为“已停止”，会放行第二次启动。
        if self.is_running:
            self.log("收到停止指令，正在安全退出…")
        self.stop_event.set()

    def _interruptible_sleep(self, seconds):
        """睡眠期间响应停止，返回 True 表示被要求停止。"""
        return self.stop_event.wait(seconds)

    # ==================================================================
    # 状态机主循环
    # ==================================================================
    def _main_loop(self):
        failures = 0
        try:
            # 预热未完成时先等（UI 不冻结，Esc 仍可停止）
            if not self._wait_ocr_ready():
                return
            while not self.stop_event.is_set():
                ok = self._one_round()
                if ok:
                    failures = 0
                else:
                    failures += 1
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        self.log(f"连续 {failures} 轮失败，自动停止。")
                        break
                limit = self.cfg.question_limit
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

        state, target = self._detect_state(ocr_lines, frame.shape[0])
        if state != self._last_state:
            self.log(f"页面状态：{state}（本页 OCR {len(ocr_lines)} 行）" +
                     (f"，按钮中心 ({int(target[0])}, {int(target[1])})"
                      if target else ""))
            self._last_state = state

        if state == STATE_GROUP_END:
            self.log("检测到组结束页面，点击“再来一组”。")
            return self._tap_pos(target, "再来一组")

        if state == STATE_RESULT:
            correct = self._extract_correct_answer(ocr_lines)
            fingerprint = self._result_fingerprint(ocr_lines, correct)
            # 点完“下一题/完成”若页面没翻过去，下一轮仍是同一张判分页；
            # 用指纹识别这种重复，避免同一题被计数两次（上限判断与
            # 截图序号都依赖 answered）。
            repeated = (fingerprint is not None
                        and fingerprint == self._last_result_fp)
            if fingerprint is not None:
                self._last_result_fp = fingerprint
            if repeated:
                self.log("判分页与上一轮相同，判定为未翻页，重新点击按钮。")
            else:
                self.stats["answered"] += 1
                if correct:
                    self.log(f"本题正确答案：{correct}")
                    if self._last_answer:
                        mine = self._normalize_answer(self._last_answer)
                        theirs = self._normalize_answer(correct)
                        self.log(f"作答对比：我方 {mine} / 正确答案 {theirs}"
                                 + ("，一致。" if mine == theirs
                                    else "，不一致。"))
                if self._shot_dir:
                    self._save_result_shot(frame, ocr_lines)
            # 按钮文案同样只看顶部区域，避免答案解析里的“完成”字样误判
            top_y = frame.shape[0] * BUTTON_TOP_RATIO
            btn = "完成" if any(t.strip() == WORD_FINISH
                             and self._box_center(b)[1] <= top_y
                             for b, t in ocr_lines) else "下一题"
            return self._tap_pos(target, btn)

        if state == STATE_SUBMITTED:
            self.log("检测到“确定”按钮，点击提交。")
            return self._tap_pos(target, "确定")

        if state == STATE_QUESTION:
            return self._handle_question(frame, ocr_lines)

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
        return not self._interruptible_sleep(self.cfg.click_response_ms / 1000.0)

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
        result = get_ocr().ocr(remove_watermark(frame), cls=False)
        if not result or not result[0]:
            return None
        return [(line[0], line[1][0]) for line in result[0]]

    def _detect_state(self, ocr_lines, frame_h):
        """按优先级判定页面状态，返回 (state, 目标按钮中心坐标或 None)。

        确定 / 下一题 / 完成 都固定在页面顶部四分之一内（实机截图确认），
        加位置约束可避免题干或答案解析里出现同样的词时误判状态。
        """
        top_zone = frame_h * BUTTON_TOP_RATIO

        def _in_top_zone(box):
            return self._box_center(box)[1] <= top_zone

        for box, text in ocr_lines:
            if WORD_GROUP_END in text:
                return STATE_GROUP_END, self._box_center(box)
        for box, text in ocr_lines:
            # 判分页：“下一题”或本组最后一题的“完成”
            if (WORD_NEXT in text or text.strip() == WORD_FINISH) \
                    and _in_top_zone(box):
                return STATE_RESULT, self._box_center(box)
        for box, text in ocr_lines:
            if text.strip() == WORD_SUBMIT and _in_top_zone(box):
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
        """从判分页取“正确答案：X”。

        只取“答案”后面紧跟的字母串：整行可能还带解析文字（如“根据 B 条
        款…”），直接 findall 会把解析里的字母也算进答案。
        """
        for _, text in ocr_lines:
            if WORD_CORRECT_ANSWER not in text:
                continue
            m = AI_PAGE_ANSWER_WORD_RE.search(text.upper())
            if not m:
                continue
            letters = re.sub(r"[,，、\s]", "", m.group(1))
            if letters:
                return ",".join(letters)
        return None

    @staticmethod
    def _normalize_answer(text):
        """答案归一化：只保留 A-H 并去重排序，用于比较两个答案。"""
        return "".join(sorted(set(re.sub(r"[^A-H]", "",
                                        (text or "").upper()))))

    @staticmethod
    def _result_fingerprint(ocr_lines, correct):
        """判分页指纹：正确答案 + 最长文本行（题干代表）。

        用于识别“点完下一题但页面没翻过去”而重复出现的同一张判分页。
        加载中遮罩会随加载完成消失，不参与指纹，避免同一题被判成两题。
        页面文本过少时指纹不可靠，返回 None 表示本轮不参与去重。
        """
        texts = [t.strip() for _, t in ocr_lines
                 if t.strip() and WORD_LOADING not in t]
        main_line = max(texts, key=len) if texts else ""
        if len(main_line) < 8:
            return None
        return f"{correct or ''}::{main_line}"

    # ------------------------------------------------------------------
    # 答题页处理
    # ------------------------------------------------------------------
    def _parse_page(self, frame, ocr_lines, frame_w, frame_h):
        """解析答题页；选项字母不足/不连续时改用左侧竖带 OCR 补齐槽位。"""
        question_text, options = parse_question_page(ocr_lines,
                                                     frame_w, frame_h)
        if len(options) >= 2 and self._letters_continuous(options):
            return question_text, options
        slots = self._detect_slots(frame, frame_w)
        if not slots:
            return question_text, options
        self.log("左侧字母竖带检出槽位：" + ",".join(ch for ch, _ in slots))
        q_new, opts_new = parse_question_page(ocr_lines, frame_w, frame_h,
                                              slot_hint=slots)
        better = (len(opts_new) > len(options)
                  or (len(opts_new) == len(options)
                      and not self._letters_continuous(options)
                      and self._letters_continuous(opts_new)))
        if better:
            return q_new or question_text, opts_new
        return question_text, options

    @staticmethod
    def _letters_continuous(options):
        """选项字母是否从 A 起连续（缺字母说明有选项没识别出来）。"""
        letters = sorted(options)
        expect = [chr(ord("A") + i) for i in range(len(letters))]
        return letters == expect

    def _detect_slots(self, frame, frame_w):
        """裁出左侧选项字母竖带单独 OCR，读出 A./B./C. 的字母列。

        字母列横向固定（约占屏宽 20%）、纵向随题长延伸；单独识别比全屏
        OCR 更稳，也能覆盖长题中被挤出屏幕的选项。只取字母与行位置，
        选项文本仍来自全屏 OCR。
        """
        band_w = max(1, int(frame_w * OPTION_BAND_RATIO))
        # 切片是视图（非连续内存），OCR 前复制一份
        lines = self._ocr_frame(np.ascontiguousarray(frame[:, :band_w]))
        return extract_slots_from_band(lines) if lines else []

    @staticmethod
    def _question_key(question_text, option_pos):
        """同一题的识别指纹：归一化题干 + 选项字母集合。

        只用于判断“是否还是上一轮那道题”，OCR 轻微抖动不应改变结论，
        所以去掉空白与标点后截断。
        """
        norm = re.sub(r"[\s\W_]+", "", (question_text or "").upper())[:60]
        return f"{norm}|{''.join(sorted(option_pos))}"

    def _handle_question(self, frame, ocr_lines):
        t_start = time.time()
        frame_h, frame_w = frame.shape[:2]
        question_text, option_pos = self._parse_page(frame, ocr_lines,
                                                    frame_w, frame_h)
        if not question_text and not option_pos:
            self.log("未能解析出题干或选项，本轮跳过。")
            return False

        # 长题模式：选项不足 2 个，或字母不从 A 起连续（缺项说明有选项
        # 被挤出屏幕）→ 滚动采集
        long_mode = (len(option_pos) < 2
                     or not self._letters_continuous(option_pos))
        if long_mode:
            self.log(f"仅检出 {len(option_pos)} 个选项（"
                     f"{','.join(sorted(option_pos)) or '无'}），疑似题干过长，"
                     "尝试滚动采集选项…")
            question_text, option_pos = self._collect_scrolled(
                question_text, option_pos, frame_w, frame_h, ocr_lines)
            if not question_text or not option_pos:
                self.log("滚动后仍无法采集到题目与选项，本轮跳过。")
                return False

        self.log(f"识别题目：{question_text[:60]}...")
        for letter, info in option_pos.items():
            self.log(f"  选项 {letter}: {info['text'] or '（未识别到文本）'}")
        self.log("选项坐标：" + str(
            {k: (int(v["pos"][0]), int(v["pos"][1]))
             for k, v in option_pos.items()}))

        # 题干 + 选项全文一起发给 AI，否则 AI 无法作答
        option_lines = "\n".join(
            f"{letter}. {info['text']}" for letter, info in option_pos.items())
        full_question = f"{question_text}\n{option_lines}"

        # 同一题重复进入（上一轮提交没成功）时复用已得到的答案与已点击
        # 记录：既不必再等一次 AI，也避免对已选中的选项再点一次
        # （多选题再点一次等于取消选中）。
        ctx = self._answer_ctx
        q_key = self._question_key(question_text, option_pos)
        if ctx is None or ctx["key"] != q_key:
            ctx = {"key": q_key, "answer": None, "clicked": set()}
            self._answer_ctx = ctx
        clicked = ctx["clicked"]
        answer = ctx["answer"]

        if answer:
            self.log(f"同一题再次进入，复用上次答案：{answer}，"
                     f"已点击：{','.join(sorted(clicked)) or '无'}")
        else:
            # 1. 发给外部 AI
            self._send_question_to_ai(full_question)

            # 2. 等待 AI 作答
            wait_ms = self.cfg.ai_wait_ms
            self.log(f"等待 AI 回答 {wait_ms}ms ...")
            if self._interruptible_sleep(wait_ms / 1000.0):
                return False

            # 2.5 部分 AI 回答后不会自动滚到页末：先滚轮翻到页底露出答案
            if self.cfg.auto_scroll and self.scroll_pos:
                if self._scroll_ai_to_bottom():
                    return False

            # 3. 读取 AI 回答（带轮询重试，应对答案生成慢的 AI）
            answer = self._wait_ai_answer(set(option_pos))
            if not answer:
                self.log("未读取到有效答案，本轮跳过。")
                return False
            self.log(f"解析后答案：{answer}")
            ctx["answer"] = answer
        self._last_answer = answer

        # 4. 逐个点击选项
        interval = self.cfg.click_response_ms / 1000.0
        if long_mode:
            # 长题模式：选项可能不在当前屏幕，需滚动搜索定位
            if not self._click_answers_scrolled(answer, frame_w, frame_h,
                                                clicked):
                return False
        else:
            for ch in answer:
                if self.stop_event.is_set():
                    return False
                if ch in clicked:
                    self.log(f"选项 {ch} 已点击过，跳过重复点击。")
                    continue
                if ch not in option_pos:
                    self.log(f"答案字母 {ch} 未找到对应选项，跳过。")
                    continue
                try:
                    self.adb.tap(*option_pos[ch]["pos"])
                except AdbError as e:
                    self.log(f"点击选项 {ch} 失败：{e}")
                    return False
                clicked.add(ch)
                self.log(f"已点击选项 {ch}")
                if self._interruptible_sleep(self.cfg.step_ms / 1000.0):
                    return False

        # 5. 提交：等页面出现"确定"后点击
        ok = self._wait_and_submit(interval)
        if ok:
            # 本题已进入判分页：清空缓存，避免“再来一组”重做同一道题时
            # 因复用已点击集合而什么都不点就直接提交
            self._answer_ctx = None
        self.log(f"本题处理完毕，耗时 {time.time() - t_start:.1f}s")
        return ok

    # ------------------------------------------------------------------
    # 长题：滚动采集与搜索点击
    # ------------------------------------------------------------------
    def _screenshot_ocr(self):
        """加锁截图 + OCR，返回 (frame, [(box, text), ...])；失败为 (None, None)。"""
        if self.adb_lock.acquire(timeout=ADB_TIMEOUT):
            try:
                frame = self.adb.screencap()
            except AdbError as e:
                self.log(f"截图失败：{e}")
                frame = None
            finally:
                self.adb_lock.release()
        else:
            return None, None
        if frame is None:
            return None, None
        return frame, self._ocr_frame(frame)

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

    def _collect_scrolled(self, question_text, option_pos, frame_w, frame_h,
                          prev_lines):
        """长题滚动采集：逐屏下滚，合并选项与题干尾部。

        页面从顶部加载，首帧题干完整；滚动只为补齐被挤出屏幕的选项，
        顺带把极端情况下滚出屏幕底部的题干尾部拼接进来。同时记录
        “字母 -> 第几屏”，供点击时直接滚到目标位置（不必从句首往下爬）。
        """
        self._scrolled_down = 0
        self._scroll_index = {ch: 0 for ch in option_pos}
        prev_texts = {t for _, t in prev_lines}
        for _ in range(MAX_LONG_Q_SCROLLS):
            if self.stop_event.is_set():
                break
            self._scroll_page(frame_w, frame_h)
            self._scrolled_down += 1
            frame, lines = self._screenshot_ocr()
            if not lines:
                continue
            q_new, opts_new = self._parse_page(frame, lines, frame_w, frame_h)
            added = 0
            for letter, info in opts_new.items():
                old = option_pos.get(letter)
                if info["text"] and (old is None or not old["text"]):
                    option_pos[letter] = info
                    added += 1
                if letter not in self._scroll_index and info["text"]:
                    self._scroll_index[letter] = self._scrolled_down
            # 题干尾部拼接（去整句重复；局部重叠允许少量冗余）
            if q_new and q_new not in question_text \
                    and question_text not in q_new:
                question_text = (question_text + " " + q_new).strip()
            # 到底检测：既没新选项、也没新文本 = 页面未再滚动
            cur_texts = {t for _, t in lines}
            new_texts = cur_texts - prev_texts
            if not added and not new_texts:
                self.log("滚动后页面无新增内容，判定已到页末，停止采集。")
                break
            prev_texts |= cur_texts
        self.log(f"滚动采集完成，共下滚 {self._scrolled_down} 屏，选项："
                 + ",".join(sorted(option_pos)))
        return question_text, option_pos

    def _find_option_on_screen(self, letter, frame_w, frame_h):
        """当前屏 OCR 找到指定字母的选项前缀框，返回点击坐标；否则 None。"""
        _, lines = self._screenshot_ocr()
        if not lines:
            return None
        # 行首字母后可能跟 . : 、 ) 或直接结束（OCR 丢了分隔符）
        pat = re.compile(rf'^\s*{letter}\s*(?:[.。．:：、)）]|$)')
        band = frame_w * OPTION_BAND_RATIO
        cands = [box for box, text in lines if pat.match(text.strip())]
        if not cands:
            return None
        # 优先竖带内的前缀框（选项字母列）；同在竖带内取最靠上的一个，
        # 避免选项正文恰好以该字母开头时被误命中
        in_band = [box for box in cands
                   if min(p[0] for p in box) <= band]
        box = min(in_band or cands, key=lambda b: self._box_center(b)[1])
        return frame_w / 2, self._box_center(box)[1]

    def _scroll_to_screen(self, letter, target, rel, frame_w, frame_h):
        """滚到第 target 屏并定位字母，返回 (点击坐标或 None, 当前屏号)。

        定位失败时在相邻屏各试一次，覆盖“选项刚好跨屏”造成的偏差。
        """
        while rel != target and not self.stop_event.is_set():
            reverse = target < rel
            self._scroll_page(frame_w, frame_h, reverse=reverse)
            rel += -1 if reverse else 1
        pos = self._find_option_on_screen(letter, frame_w, frame_h)
        if pos is None and rel > 0:
            self._scroll_page(frame_w, frame_h, reverse=True)
            rel -= 1
            pos = self._find_option_on_screen(letter, frame_w, frame_h)
        if pos is None and rel < MAX_LONG_Q_SCROLLS:
            self._scroll_page(frame_w, frame_h)
            rel += 1
            pos = self._find_option_on_screen(letter, frame_w, frame_h)
        return pos, rel

    def _click_answers_scrolled(self, answer, frame_w, frame_h, clicked):
        """长题模式：按字母所在屏号滚到目标位置再点击；找不到则弃题点 A。"""
        rel = self._scrolled_down      # 相对页面顶部的下滚屏数
        index = self._scroll_index
        step_s = self.cfg.step_ms / 1000.0
        # 先点离当前屏最近的字母，减少来回滚动
        ordered = sorted(answer,
                         key=lambda ch: (abs(index.get(ch, rel) - rel), ch))
        for ch in ordered:
            if self.stop_event.is_set():
                return False
            if ch in clicked:
                self.log(f"选项 {ch} 已点击过，跳过重复点击。")
                continue
            pos, rel = self._scroll_to_screen(ch, index.get(ch, rel), rel,
                                              frame_w, frame_h)
            if pos is None:
                self.log(f"滚动后仍未找到选项 {ch}，放弃本题，改点 A 提交。")
                pos, rel = self._scroll_to_screen("A", 0, rel,
                                                  frame_w, frame_h)
                if pos is None:
                    return False
            try:
                self.adb.tap(*pos)
            except AdbError as e:
                self.log(f"点击选项 {ch} 失败：{e}")
                return False
            clicked.add(ch)
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
                        state, pos = self._detect_state(lines, frame.shape[0])
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
        prompt = self.cfg.prompt
        pyperclip.copy(f"{prompt}\n{question_text}")
        pyautogui.click(*self.ai_input_pos)
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        pyautogui.click(*self.ai_send_pos)
        self.log("题目已发送给外部 AI。")

    def _scroll_ai_to_bottom(self):
        """鼠标移到 AI 对话区滚动位置，滚轮向下滚动到页末。

        部分 AI 网页回答后不会自动滚到页底，答案不在可见区域内，
        需先滚动暴露答案再读取。返回 True 表示期间被要求停止。
        """
        x, y = self.scroll_pos
        pyautogui.moveTo(x, y, duration=0.2)
        for _ in range(AI_SCROLL_CLICKS):
            if self.stop_event.is_set():
                return True
            pyautogui.scroll(-AI_SCROLL_UNITS, x=x, y=y)
            if self._interruptible_sleep(0.05):
                return True
        # 全部滚动结束后等待页面稳定，再进行 OCR 识别
        settle_ms = self.cfg.scroll_settle_ms
        self.log(f"已滚动 AI 对话区到页末，等待 {settle_ms}ms 后识别答案 ...")
        return self._interruptible_sleep(settle_ms / 1000.0)

    def _read_ai_answer(self, valid_letters=None, dump=False):
        """截图整个 AI 页面并 OCR，从干扰中提纯答案字母。

        AI 会在答案前后额外说话（“已完成分析”“需要我帮你整理…吗？”、
        复制/点赞等图标行、推荐追问气泡），题目气泡内又有 A./B./C. 开头的
        选项行和大量英文干扰（Git/final 等），不能全文直接抓字母：
        1. 用已发送 Prompt 的开头文字定位当前题目气泡顶部（取页面最后一次
           出现，跳过历史轮次的气泡）；
        2. 气泡顶部以下、最低的选项前缀行视为气泡底部，答案只可能在其下方；
        3. 气泡下方取第一条“独立答案字母行”（如 "A"、"A,C"），其次兜底
           “答案：X”显式行；候选必须只由本题有效选项字母构成，
           排除孤立英文单词类误识别；
        4. 页面过长气泡已滚出屏幕时（找不到 Prompt），改取页面最下方
           一条独立答案字母行（即最近一次回答）。
        """
        if not self.ai_page_roi:
            return None
        x1, y1, x2, y2 = (int(v) for v in self.ai_page_roi)
        with mss.mss() as sct:
            shot = np.array(sct.grab({"left": x1, "top": y1,
                                      "width": x2 - x1, "height": y2 - y1}))[:, :, :3]
        result = get_ocr().ocr(shot, cls=False)
        if not result or not result[0]:
            return None
        lines = [(self._box_center(line[0])[1], line[1][0])
                 for line in result[0]]           # [(行中心 y, 文本), ...]

        prompt_head = self.cfg.prompt[:8]
        marker_y = None
        if prompt_head:
            hit = [cy for cy, t in lines if prompt_head in t.replace(" ", "")]
            if hit:
                marker_y = max(hit)               # 当前题目气泡顶部

        floor_y = marker_y
        if marker_y is not None:
            opt_ys = [cy for cy, t in lines
                      if cy > marker_y and AI_PAGE_OPTION_RE.match(t)]
            if opt_ys:
                floor_y = max(opt_ys)             # 气泡底部 = 最低选项行

        ordered = sorted(lines, key=lambda lt: lt[0])
        if floor_y is not None:
            cands = [t for cy, t in ordered if cy > floor_y]   # 只看气泡下方
        else:
            cands = [t for cy, t in ordered]                   # 气泡离屏：全页查找

        def _scan(strict):
            """一轮扫描：strict 要求整行仅由有效选项字母构成（防英文单词
            误识别）；宽松轮则取有效选项交集。气泡可见返回最上方命中行，
            离屏返回最下方（最近一次）命中行。"""
            found = None
            for t in cands:
                up = t.upper()
                m = AI_PAGE_LETTER_RE.match(up) or AI_PAGE_ANSWER_WORD_RE.search(up)
                if not m:
                    continue
                letters = re.sub(r"[,，、\s]", "", m.group(1))
                if valid_letters:
                    if strict:
                        if not set(letters) <= set(valid_letters):
                            continue                           # 含非选项字母，判为干扰
                    else:
                        letters = "".join(c for c in letters
                                          if c in valid_letters)
                        if not letters:
                            continue
                found = (t, letters)
                if floor_y is not None:
                    return found
            return found

        hit = _scan(True)
        if hit is None:
            hit = _scan(False)
            if hit:
                self.log(f"答案行含未识别选项字母，宽松取交集：{hit[1]}")
        if hit:
            t, letters = hit
            if floor_y is None:
                self.log(f"题目气泡不在画面内，取页面最下方答案行：{t}")
            else:
                self.log(f"AI 页面答案行原文：{t}")
            return letters
        if dump:   # 最终失败时转储页面识别文本，便于排查
            self.log("AI 页面 OCR 行转储：" +
                     " | ".join(t for _, t in ordered)[:300])
        return None

    def _wait_ai_answer(self, valid_letters):
        """轮询读取 AI 答案：固定等待期满时页面可能仍未生成完答案
        （深度检索类 AI 普遍 15 秒以上），每 AI_ANSWER_POLL_S 秒重试一次，
        最长再等 AI_ANSWER_RETRY_S 秒；勾选“自动滚动到页末”时每次重试前
        重新滚动，保证持续变长的答案保持在画面内。"""
        deadline = time.time() + AI_ANSWER_RETRY_S
        retries = 0
        while not self.stop_event.is_set():
            answer = self._read_ai_answer(valid_letters)
            if answer:
                if retries:
                    self.log(f"答案在第 {retries + 1} 次重试识别中出现。")
                return answer
            if time.time() >= deadline:
                break
            retries += 1
            self.log(f"答案尚未出现在页面上，{AI_ANSWER_POLL_S} 秒后重试识别…")
            if self._interruptible_sleep(AI_ANSWER_POLL_S):
                return None
            if self.cfg.auto_scroll and self.scroll_pos:
                self._scroll_ai_to_bottom()
        self.log(f"重试 {retries} 次仍未读到答案，转储页面文本辅助排查。")
        return self._read_ai_answer(valid_letters, dump=True)

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
        self._flush_params()   # 关窗前把未落盘的参数写完
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
