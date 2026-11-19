"""大扫除小助手 V3.3 —— 手机答题自动化桌面应用（主程序）。

窗口由 ui_panels.py 构建（三列：手机预览 / 选项标签 / 设置与日志），
运行逻辑拆在各自的模块里，本文件只保留程序外壳：

  app_state.py     页面状态常量、AI 答案正则、RunConfig 参数快照
  layout_profile.py  布局比例与水印阈值的唯一来源
  ocr_engine.py    PaddleOCR 懒加载单例 + 后台预热
  device.py        adb 设备管理与手机实时预览
  vision.py        截图 OCR、页面状态判定、题干/选项解析
  ai_bridge.py     外部 AI 窗口交互（发题 / 滚动 / 提纯答案）
  automation.py    状态机主循环、长题滚动、提交
  calibration.py   AI 窗口标定
  ui_panels.py     三列 UI 与四个设置面板

手机端操作全部走 adb（截图 + tap），元素位置运行时 OCR 自动定位，不落盘；
外部 AI 窗口（AI 页面区/输入框/发送键）仅运行期标定，关闭即清除；
仅 Prompt 与延时参数持久化到 params.json。
"""

import importlib
import importlib.util
import json
import logging
import os
import threading
import time
from logging.handlers import RotatingFileHandler

import tkinter as tk

from adb_service import AdbService
from ai_bridge import AiBridgeMixin
from app_state import PARAMS_SAVE_DEBOUNCE_MS, RunConfig
from automation import AutomationMixin
from calibration import CalibrationMixin
from config import (APP_VERSION, DEFAULT_AI_WAIT_MS, DEFAULT_CLICK_RESPONSE_MS,
                    DEFAULT_PROMPT, DEFAULT_QUESTION_LIMIT,
                    DEFAULT_SCRCPY_OPTIONS, DEFAULT_SCROLL_SETTLE_MS,
                    DEFAULT_STEP_MS, LOG_DIR, PARAMS_FILE, SCRCPY_OPTIONS,
                    SHOT_DIR)
from device import DeviceMixin
from ocr_engine import (is_ocr_ready, ocr_error, wait_ocr_ready, warm_up_ocr)
from ui_panels import (PREVIEW_COL_WIDTH, WINDOW_HEIGHT, WINDOW_WIDTH,
                       UIPanelsMixin)
from ui_widgets import DEFAULT_THEME, apply_theme
from vision import VisionMixin

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


class OCRApp(DeviceMixin, VisionMixin, AiBridgeMixin, AutomationMixin,
             CalibrationMixin, UIPanelsMixin):
    """程序外壳：窗口生命周期、日志、参数持久化、启停。"""

    def __init__(self):
        self._logger = _setup_file_logging()
        self.root = tk.Tk()
        self.root.title(APP_VERSION)

        # --- 界面外观（颜色与置顶要在建控件前定：控件颜色是构造时取的） ---
        saved = self._read_params_raw()
        self._ui_theme = apply_theme(saved.get("ui_theme", DEFAULT_THEME))
        self._pinned = bool(saved.get("window_topmost", False))
        self.root.attributes("-topmost", self._pinned)
        # 左列预览可折叠：默认展开，收起时窗口宽度少一列（不留给右列）；
        # x 反向补回一列，让两种形态的右边界落在同一位置，启动后
        # 中列与右列的位置与上次使用一致
        self._preview_visible = bool(saved.get("preview_visible", True))
        if self._preview_visible:
            width, x = WINDOW_WIDTH, 320
        else:
            width, x = WINDOW_WIDTH - PREVIEW_COL_WIDTH, 320 + PREVIEW_COL_WIDTH
        self.root.geometry(f"{width}x{WINDOW_HEIGHT}+{x}+20")

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
        # 换配色重建控件期间旧 log_area 已销毁：先攒着，重建完再按顺序补回
        if getattr(self, "_ui_rebuilding", False):
            self._pending_logs.append(message)
            return
        try:
            if not self.log_area.winfo_exists():
                return
            # 时间戳用灰色 tag（与日志正文区分），与 UI 的 MAA 风格一致
            start = self.log_area.index("end-1c")
            self.log_area.insert(tk.END, time.strftime("%H:%M:%S") + "  ", "ts")
            self.log_area.insert(tk.END, message + "\n")
            # 整行挂 log tag：折行后的部分才会按 lmargin2 缩进到正文列
            self.log_area.tag_add("log", start, self.log_area.index("end-1c"))
            self.log_area.see(tk.END)
        except tk.TclError:
            pass                          # 窗口正在关闭，丢掉这一条即可

    # ==================================================================
    # 参数持久化（仅参数，元素位置一律不落盘）
    # ==================================================================
    def _load_params(self, quiet=False):
        """读取参数并回填界面；quiet=True 时不打日志（换配色重建时用）。"""
        params = self._read_params_raw()

        # 外观：主题与置顶状态也跟参数一起恢复
        theme = params.get("ui_theme", DEFAULT_THEME)
        if theme != self._ui_theme:          # 同主题不重复换（会清位图缓存）
            self._ui_theme = apply_theme(theme)
        self._pinned = bool(params.get("window_topmost", False))
        self.root.attributes("-topmost", self._pinned)
        self._preview_visible = bool(params.get("preview_visible", True))
        if hasattr(self, "pin_button"):
            self._apply_preview_visibility()
            self._sync_preview_button()
            self._sync_pin_button()
            self._sync_theme_swatches()

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
        # 连接时追加的设置项：参数文件里没有这一项时用默认（锁屏 + 亮度）
        saved_opts = params.get("scrcpy_options")
        if not isinstance(saved_opts, list):
            saved_opts = list(DEFAULT_SCRCPY_OPTIONS)
        for key, var in getattr(self, "_scrcpy_vars", {}).items():
            var.set(key in saved_opts)
        if not quiet:
            self.log("参数已加载。")

    @staticmethod
    def _read_params_raw():
        """读 params.json；文件缺失或损坏时返回空 dict。"""
        try:
            with open(PARAMS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

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
            "ui_theme": self._ui_theme,
            "window_topmost": bool(self._pinned),
            "preview_visible": bool(self._preview_visible),
            "scrcpy_options": [opt["key"] for opt in SCRCPY_OPTIONS
                               if self._scrcpy_var(opt["key"])],
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
            self.log("请先完成“外部 AI 窗口标定”的三项内容。")
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
