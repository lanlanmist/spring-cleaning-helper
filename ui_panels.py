"""主窗口构建：三列布局 + 中间标签切换。

视觉风格：暖灰底 + 白色圆角卡片 + 强调色（默认暖驼棕，可切换），
主色只用于选中态；整页配色是一套主题，换主题靠重建控件实现。

布局：
  左列  手机实时画面预览（无边框，底色与页面一致，只有画面；可折叠）；
  中列  顶部两个开关按钮（预览展开/收起、窗口置顶），下方是选项标签卡片（ADB 设备 /
        外部 AI 窗口标定 / AI 指令 / 延时参数），最下面是配色色板；
  右列  上半是当前标签对应的设置面板，下半是勿扰提示胶囊 + 并排按钮 + 日志。

控件属性名（device_combo / prompt_text / log_area / btn_run …）保持不变，
参数读写与业务逻辑无需感知 UI 结构变化。
"""

import re
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import ui_widgets
from config import DEFAULT_SCRCPY_OPTIONS, PREVIEW_WIDTH, SCRCPY_OPTIONS
from ui_widgets import (ACCENT, ACCENT_DARK, ACCENT_SOFT, BG, BORDER, CARD,
                        INK, MONO, MUTED, PILL_BG, PILL_BORDER, TEXT,
                        ColorSwatch, RoundButton, RoundCard, RoundCheck,
                        RoundCombo, RoundEntry, SlimScrollbar, THEME_ORDER,
                        THEMES, apply_theme, resolve_font, ui_font)

# (标签键, 标签文字) —— 顺序即中间列的排列顺序
TABS = (
    ("adb", "ADB 设备"),
    ("calib", "外部 AI 窗口标定"),
    ("prompt", "AI 指令"),
    ("params", "延时参数"),
)

# 标签按钮（含置顶按钮）尺寸
TAB_WIDTH = 112
TAB_HEIGHT = 54

# 窗口与左列的基准尺寸：左列可折叠，窗口宽度按展开/收起增减
WINDOW_WIDTH = 1080
WINDOW_HEIGHT = 800
PREVIEW_COL_WIDTH = PREVIEW_WIDTH + 20     # 预览画布 + 左右留白


def sync_palette():
    """把 ui_widgets 当前主题的常量刷到本模块的全局名。

    `from ui_widgets import ACCENT` 是值拷贝，换主题后本模块拿到的还是
    旧值；重建控件前调一次这里，旧名字就被新颜色覆盖。
    """
    g = globals()
    for name in ui_widgets.PALETTE_NAMES:
        g[name] = getattr(ui_widgets, name)


class UIPanelsMixin:
    """三列主窗口、四个设置面板与两个开关。"""


    # ==================================================================
    # 骨架与 ttk 主题
    # ==================================================================
    def _init_style(self):
        """ttk 只用于滚动条与下拉列表，其余控件全部自绘圆角。"""
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=CARD, foreground=TEXT,
                        font=ui_font(10))
        # 下拉列表（listbox）不归 ttk 管，只能走 option 数据库
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_SOFT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        self.root.option_add("*TCombobox*Listbox.borderWidth", 1)

    def _setup_ui(self):
        self.root.configure(bg=BG)
        resolve_font(self.root)
        self._init_style()

        self.root.grid_rowconfigure(0, weight=1)
        # 左列宽度固定为预览宽度（可折叠，收起时由 _apply_preview_visibility 归零），
        # 中列固定为标签列，右列吃掉剩余空间
        self.root.grid_columnconfigure(0, minsize=PREVIEW_COL_WIDTH)
        self.root.grid_columnconfigure(1, minsize=TAB_WIDTH + 28)
        self.root.grid_columnconfigure(2, weight=1)

        self._build_columns()

    def _build_columns(self):
        """建三列（换主题时会被整组拆掉重建，所以单独一个方法）。"""
        self._col_left = tk.Frame(self.root, bg=BG)
        self._col_left.grid(row=0, column=0, sticky="nsew", padx=(10, 5),
                            pady=10)
        self._col_mid = tk.Frame(self.root, bg=BG)
        self._col_mid.grid(row=0, column=1, sticky="nsew", pady=10)
        self._col_right = tk.Frame(self.root, bg=BG)
        self._col_right.grid(row=0, column=2, sticky="nsew", padx=(5, 10),
                             pady=10)

        self._build_preview_ui(self._col_left)
        self._build_tab_column(self._col_mid)
        self._build_right_column(self._col_right)
        self._apply_preview_visibility()
        self._show_tab(getattr(self, "_active_tab", TABS[0][0]))

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    @staticmethod
    def _panel_title(parent, text):
        tk.Label(parent, text=text, bg=CARD, fg=TEXT,
                 font=ui_font(11, "bold")).pack(anchor="w", padx=8,
                                                pady=(10, 8))

    @staticmethod
    def _caption(parent, text, padx=8, pady=(0, 0)):
        tk.Label(parent, text=text, bg=CARD, fg=MUTED, font=ui_font(9),
                 justify="left", wraplength=470).pack(
            anchor="w", padx=padx, pady=pady)

    @staticmethod
    def _ghost_button(parent, text, command, height=34, **kw):
        """白底描边的次要按钮。"""
        return RoundButton(parent, text, command, height=height, radius=9,
                           bg=CARD, fill=CARD, fg=INK, outline=BORDER,
                           hover=ACCENT_SOFT, size=10, **kw)

    @staticmethod
    def _main_button(parent, text, command, height=36, **kw):
        """实底强调色的主要按钮。"""
        return RoundButton(parent, text, command, height=height, radius=9,
                           bg=CARD, fill=ACCENT, fg="white", outline=ACCENT,
                           hover=ACCENT_DARK, size=10, bold=True, **kw)

    def _entry(self, parent, width=104):
        e = RoundEntry(parent, width=width, height=32, radius=9, bg=CARD,
                       size=10)
        e.bind("<FocusOut>", lambda ev: self._save_params())
        return e

    def _make_text(self, parent, **kw):
        """圆角卡片 + Text + 自绘细滚动条。"""
        card = RoundCard(parent, radius=12, pad=8)
        text = tk.Text(card.body, relief="flat", bd=0, highlightthickness=0,
                       **kw)
        bar = SlimScrollbar(card.body, command=text.yview, bg=CARD)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y", padx=(0, 2))
        text.pack(side="left", fill="both", expand=True)
        text.bind("<MouseWheel>",
                  lambda e: text.yview_scroll(-1 if e.delta > 0 else 1,
                                              "units") or "break")
        return card, text

    # ==================================================================
    # 左列：手机实时画面
    # ==================================================================
    def _build_preview_ui(self, parent):
        # 底色跟页面一致：没连设备时只是一块空白，不是一整屏黑
        self.preview_canvas = tk.Canvas(parent, bg=BG, bd=0,
                                        highlightthickness=0,
                                        width=1, height=1)
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.bind("<Configure>",
                                 lambda e: self._draw_preview_frame())

    def _draw_preview_frame(self):
        """没画面时框出预览区域，边框颜色与其它卡片一致。"""
        c = self.preview_canvas
        c.delete("frame")
        if c.find_withtag("photo"):
            return                  # 有画面就不画框，免得框贴在画面外
        w, h = c.winfo_width(), c.winfo_height()
        if w > 4 and h > 4:
            c.create_rectangle(1, 1, w - 1, h - 1, outline=BORDER, tags="frame")

    # ==================================================================
    # 中列：预览开关 + 置顶开关 + 选项标签 + 配色色板
    # ==================================================================
    def _build_tab_column(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        # _pinned / _preview_visible 由外部（参数加载/切换）维护，
        # 这里只负责把它们画出来

        # 顶部两个开关框：预览控制左列是否展开，置顶控制窗口层级
        self.preview_button = RoundButton(parent, "预览已开", self._toggle_preview,
                                          height=38, radius=10, fill=ACCENT,
                                          fg="white", outline=ACCENT,
                                          hover=ACCENT_DARK, size=11, bold=True)
        self.preview_button.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.pin_button = RoundButton(parent, "已置顶", self._toggle_topmost,
                                      height=38, radius=10, fill=ACCENT,
                                      fg="white", outline=ACCENT,
                                      hover=ACCENT_DARK, size=11, bold=True)
        self.pin_button.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        card = RoundCard(parent, radius=14, pad=6, bg=BG)
        card.grid(row=2, column=0, sticky="new")

        self._tab_buttons = {}
        for i, (key, label) in enumerate(TABS):
            btn = RoundButton(card.body, label, lambda k=key: self._show_tab(k),
                              width=TAB_WIDTH, height=TAB_HEIGHT, radius=10,
                              bg=CARD, fill=CARD, fg=MUTED, outline=CARD,
                              hover=ACCENT_SOFT, size=11, wrap=TAB_WIDTH - 12)
            btn.pack(fill="x", pady=(0 if i == 0 else 4, 0))
            self._tab_buttons[key] = btn

        # 色板独立贴在中列最下方（不放进标签卡片里）
        self._build_theme_swatches(parent)
        self._sync_preview_button()
        self._sync_pin_button()

    def _build_theme_swatches(self, parent):
        """中列最底部的一排小色块：点一下就换整页配色。"""
        parent.grid_rowconfigure(3, weight=1)      # 空行吃掉中间空白，把色板压到底
        host = tk.Frame(parent, bg=BG)
        host.grid(row=3, column=0, sticky="sew", pady=(0, 8))
        row = tk.Frame(host, bg=BG)
        row.pack(side="bottom")
        self._theme_swatches = {}
        for i, key in enumerate(THEME_ORDER):
            sw = ColorSwatch(row, THEMES[key]["accent"],
                             lambda k=key: self._switch_theme(k),
                             bg=BG)
            sw.pack(side="left", padx=(0 if i == 0 else 8, 0))
            self._theme_swatches[key] = sw
        self._sync_theme_swatches()

    def _sync_theme_swatches(self):
        for key, sw in getattr(self, "_theme_swatches", {}).items():
            sw.set_selected(key == self._ui_theme)

    # ------------------------------------------------------------------
    # 换配色：整页重建（控件颜色是构造时取的，改不了只能重建）
    # ------------------------------------------------------------------
    def _switch_theme(self, key):
        if key == self._ui_theme or key not in THEMES:
            return
        if self.is_running:
            self.log("任务运行中，先停止再切换配色。")
            return
        old_name = THEMES[self._ui_theme]["name"]

        # 0) 把挂起的 idle 先跑完：刚改过窗口尺寸时 Text 还有待重排的回调，
        #    直接 destroy 会让 Tk 报 invalid command name
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass

        # 1) 先把界面上没有落盘的内容存进去（重建会丢掉输入框里的值）
        #    日志用 dump 存，带上 ts / log 两个 tag：只存纯文本的话
        #    时间戳会变回正文色，折行也不再缩进
        log_dump = self.log_area.dump("1.0", "end-1c", text=True, tag=True)

        # 2) 换调色板：ui_widgets 落新值，再刷到本模块的同名全局
        self._ui_theme = apply_theme(key)
        sync_palette()
        self._flush_params()            # 连同新主题一起落盘

        # 3) 拆掉三列重建（ttk 样式也要按新色重配）
        #    重建期间旧控件已销毁、新控件未建，后台线程打进来的日志先攒着，
        #    否则 after(0, _append_log) 会落到已销毁的 old log_area 上报错
        self._ui_rebuilding = True
        self._pending_logs = []
        pending = []
        try:
            for col in (self._col_left, self._col_mid, self._col_right):
                col.destroy()
            self._init_style()
            self._preview_size = None   # 新画布尺寸未定，重新量
            self._build_columns()

            # 4) 回填状态
            self._load_params(quiet=True)    # 少一行“参数已加载”噪声
            if log_dump:
                self._restore_log(log_dump)
                self.log_area.see(tk.END)
            if getattr(self, "_device_list", None):
                self._fill_device_combo(self._device_list)   # 新控件重新填设备
        finally:
            self._ui_rebuilding = False
            pending, self._pending_logs = self._pending_logs, []

        for msg in pending:                  # 攒下的日志按原顺序补回去
            self._append_log(msg)

        self.log(f"配色已切换为「{THEMES[key]['name']}」（原「{old_name}」）。")

    def _restore_log(self, dump):
        """把 dump 出来的日志文本与 tag 重铺到新控件上。

        先铺完文本再加 tag：tag_add 只能作用在已存在的字符上。
        """
        ta = self.log_area
        for kind, value, _index in dump:
            if kind == "text":
                ta.insert(tk.END, value)
        opened = {}
        for kind, value, index in dump:
            if kind == "tagon":
                opened[value] = index
            elif kind == "tagoff":
                start = opened.pop(value, None)
                if start is not None:
                    ta.tag_add(value, start, index)
        for tag, start in opened.items():
            ta.tag_add(tag, start, "end-1c")

    def _show_tab(self, key):
        """切换右侧面板并高亮当前标签。

        四页共用同一格：面板区高度由最高的一页决定，切标签时下方的按钮
        与日志不会上下跳动。行高在首帧量出后被冻住（见 _freeze_panel_row），
        之后只留当前页参与布局，窗口重排不用白算另外三页。
        """
        self._active_tab = key
        self._sync_panels_visible(key)
        self._panels[key].tkraise()
        for k, btn in self._tab_buttons.items():
            on = k == key
            btn.set_state(fill=ACCENT if on else CARD,
                          fg="white" if on else MUTED,
                          outline=ACCENT if on else CARD,
                          hover=ACCENT_DARK if on else ACCENT_SOFT,
                          bold=on)

    def _sync_panels_visible(self, key):
        """只让当前页参与 grid 布局。

        行高还没冻结时四页都留着——那一轮正是靠“四页同时摆”量出的
        最高页高度；冻结之后再隐藏其余三页。
        """
        frozen = getattr(self, "_panel_row_height", None) is not None
        for k, panel in self._panels.items():
            if frozen and k != key:
                panel.grid_remove()
            else:
                panel.grid()

    def _freeze_panel_row(self, host, _tries=0):
        """把面板区行高钉在“最高的一页”上，之后只保留当前页参与布局。

        实测：四页同时参与重排时，每次窗口 resize 要 40ms；只剩一页时
        21ms，差异全在另外三页的 layout 上。
        """
        if host is not getattr(self, "_panel_host", None):
            return                   # 已被换配色重建掉的旧 host，丢掉这次量测
        try:
            h = host.body.winfo_height()
        except tk.TclError:
            return
        if h <= 1:                   # 窗口还没映射/布局没落地，过一会再量
            if _tries < 10:
                host.body.after(50, lambda: self._freeze_panel_row(host,
                                                                  _tries + 1))
            return
        self._panel_row_height = h
        host.body.grid_rowconfigure(0, minsize=h)
        self._sync_panels_visible(getattr(self, "_active_tab", TABS[0][0]))


    # ------------------------------------------------------------------
    # 预览开关（左列可折叠）
    # ------------------------------------------------------------------
    def _apply_preview_visibility(self):
        """按 _preview_visible 显示/隐藏左列。

        只动列宽与 grid 状态，不改窗口尺寸：启动与换配色重建时窗口宽
        已经是对的，由调用方自己决定要不要跟着变宽/变窄。
        """
        show = getattr(self, "_preview_visible", True)
        self.root.grid_columnconfigure(0, minsize=PREVIEW_COL_WIDTH if show else 0)
        if show:
            self._col_left.grid()
        else:
            self._col_left.grid_remove()

    def _toggle_preview(self):
        """展开/收起左列预览：左边界伸缩，右边界与右列宽度都不动。

        一次性到位、不做逐帧动画：这套界面重排一次就要 40~130ms（Text 折行、
        整树 layout、整窗重绘），拆成 4 帧反而累成 500ms 的断续拖动，体感比
        直接切换更差，所以这里只保留按钮本身的颜色过渡。
        """
        self._preview_visible = not self._preview_visible
        self._sync_preview_button()
        self._write_params()          # 预览展开状态也持久化
        self.log("预览已展开。" if self._preview_visible else "预览已收起。")

        w, h, x, y = self._window_geom()
        if self._preview_visible:
            self._col_left.grid()     # 先恢复显示，再一次性把窗口撑到满宽
            self.root.grid_columnconfigure(0, minsize=PREVIEW_COL_WIDTH)
            self.root.geometry(f"{w + PREVIEW_COL_WIDTH}x{h}+"
                               f"{x - PREVIEW_COL_WIDTH}+{y}")
        else:
            self.root.grid_columnconfigure(0, minsize=0)
            self._col_left.grid_remove()
            self.root.geometry(f"{w - PREVIEW_COL_WIDTH}x{h}+"
                               f"{x + PREVIEW_COL_WIDTH}+{y}")
        # 提交一次：连续点击时才能读到已生效的几何，否则会在旧值上叠算
        self.root.update_idletasks()

    def _window_geom(self):
        """当前窗口 (宽, 高, x, y)，与 geometry()/wm_geometry() 同一坐标。"""
        m = re.match(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", self.root.geometry())
        if not m:
            return (self.root.winfo_width(), self.root.winfo_height(),
                    self.root.winfo_x(), self.root.winfo_y())
        w, h, x, y = m.groups()
        return int(w), int(h), int(x), int(y)

    def _sync_preview_button(self):
        if getattr(self, "_preview_visible", True):
            self.preview_button.set_text("预览已开")
            self.preview_button.set_state(fill=ACCENT, fg="white", outline=ACCENT,
                                          hover=ACCENT_DARK, bold=True)
        else:
            self.preview_button.set_text("预览已关")
            self.preview_button.set_state(fill=CARD, fg=MUTED, outline=BORDER,
                                          hover=ACCENT_SOFT, bold=False)

    # ------------------------------------------------------------------
    # 置顶开关
    # ------------------------------------------------------------------
    def _sync_pin_button(self):
        if self._pinned:
            self.pin_button.set_text("已置顶")
            self.pin_button.set_state(fill=ACCENT, fg="white", outline=ACCENT,
                                      hover=ACCENT_DARK, bold=True)
        else:
            self.pin_button.set_text("未置顶")
            self.pin_button.set_state(fill=CARD, fg=MUTED, outline=BORDER,
                                      hover=ACCENT_SOFT, bold=False)

    def _toggle_topmost(self):
        self._pinned = not self._pinned
        self.root.attributes("-topmost", self._pinned)
        self._sync_pin_button()
        self._write_params()          # 置顶状态也持久化
        self.log("窗口已置顶。" if self._pinned else "窗口已取消置顶。")

    # ==================================================================
    # 右列：面板 + 底部
    # ==================================================================
    def _build_right_column(self, parent):
        # 面板区高度由最高面板的内容决定（四页共用同一行），剩余空间全给底部日志
        parent.grid_rowconfigure(0, weight=0)
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        # 面板卡片：与左侧预览卡片顶部齐平
        host = RoundCard(parent, radius=14, pad=10, bg=BG)
        host.grid(row=0, column=0, sticky="nsew")
        host.body.grid_rowconfigure(0, weight=1)
        host.body.grid_columnconfigure(0, weight=1)
        self._panel_host = host
        self._panel_row_height = None      # 行高冻结值，首帧量出来再填

        # 四个面板叠放在同一格，切换时用 tkraise 置顶
        self._panels = {}
        builders = {
            "adb": self._build_device_panel,
            "calib": self._build_calib_panel,
            "prompt": self._build_prompt_panel,
            "params": self._build_param_panel,
        }
        for key, _label in TABS:
            panel = tk.Frame(host.body, bg=CARD)
            panel.grid(row=0, column=0, sticky="nsew")
            builders[key](panel)
            self._panels[key] = panel

        # 首帧后把行高钉在最高的一页，并只留当前页参与布局（见 _freeze_panel_row）。
        # 用 after 而不是 after_idle：idle 回调会和布局 handler 抢同一个批次，
        # 那时控件还都是 1px，量不到真高度。
        host.body.after(50, lambda: self._freeze_panel_row(host))

        bottom = tk.Frame(parent, bg=BG)
        bottom.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self._build_bottom_ui(bottom)

    # ------------------------------------------------------------------
    # 面板：ADB 设备
    # ------------------------------------------------------------------
    def _build_device_panel(self, parent):
        self._panel_title(parent, "ADB 设备")

        self.device_combo = RoundCombo(parent, height=34, radius=9, bg=CARD,
                                       size=10)
        self.device_combo.pack(fill="x", padx=8, pady=(0, 8))
        # 下拉框被点开又关掉时 Tk 偶尔会把显示清空，失焦时补回来
        self.device_combo.bind("<FocusOut>",
                               lambda e: self._restore_device_text())

        # 状态行放在下拉框下面：比原来贴在按钮下方更合理（先看设备再看操作）
        self.device_info_label = tk.Label(parent, text="未刷新", fg=MUTED,
                                          bg=CARD, font=ui_font(10),
                                          justify="left", wraplength=470)
        self.device_info_label.pack(anchor="w", padx=12, pady=(0, 10))

        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", padx=8)
        self._ghost_button(row, "刷新设备",
                           self.refresh_devices).pack(side="left")
        # 与刷新按钮相邻：两个动作本就前后相接，靠右会让人以为是两件事
        self._main_button(row, "连接", self.connect_device,
                          width=96).pack(side="left", padx=(10, 0))

        self._caption(parent, "提示：手机需先开启 USB 调试并授权；下方勾选项会在点"
                              "「连接」时一并生效。",
                      padx=12, pady=(12, 0))

        # --- 连接时追加的 scrcpy 参数：勾选后才拼进启动命令 ---
        grid = tk.Frame(parent, bg=CARD)
        grid.pack(fill="x", padx=10, pady=(8, 0))
        for col in range(3):
            grid.grid_columnconfigure(col, weight=1)
        self._scrcpy_vars = {}
        for i, opt in enumerate(SCRCPY_OPTIONS):
            var = tk.BooleanVar(value=opt["key"] in DEFAULT_SCRCPY_OPTIONS)
            self._scrcpy_vars[opt["key"]] = var
            RoundCheck(grid, opt["label"], var, command=self._save_params).grid(
                row=i // 3, column=i % 3, sticky="w", pady=(0, 4))

    # ------------------------------------------------------------------
    # 面板：外部 AI 窗口标定
    # ------------------------------------------------------------------
    def _build_calib_panel(self, parent):
        self._panel_title(parent, "外部 AI 窗口标定")
        self._caption(parent, "标定结果仅本次运行有效，关闭程序即清除", padx=12,
                      pady=(0, 10))

        self._main_button(parent, "一键顺序标定",
                          self.calibrate_all).pack(fill="x", padx=12)

        grid = tk.Frame(parent, bg=CARD)
        grid.pack(fill="x", padx=12, pady=8)
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)
        pairs = (("重新框选 AI 页面", self.select_ai_page),
                 ("重新点选输入框", self.select_ai_input),
                 ("重新点选发送键", self.select_ai_send),
                 ("设定滚动位置", self.select_scroll_pos))
        for i, (text, cmd) in enumerate(pairs):
            btn = self._ghost_button(grid, text, cmd, width=180)
            btn.grid(row=i // 2, column=i % 2, sticky="ew", pady=(0, 6),
                     padx=(0, 4) if i % 2 == 0 else (4, 0))

        self.auto_scroll_var = tk.BooleanVar(value=False)
        RoundCheck(parent, "自动滚动到页末", self.auto_scroll_var,
                   command=self._save_params).pack(anchor="w", padx=10)
        self._caption(parent, "勾选后：等 AI 作答完毕，鼠标移至此位置向下滚动，"
                              "再读取答案。", padx=26, pady=(2, 0))

        self.calib_status = tk.Label(parent, text="", fg=MUTED, bg=CARD,
                                     font=ui_font(10), justify="left",
                                     wraplength=470)
        self.calib_status.pack(anchor="w", padx=12, pady=(12, 0))
        self._update_calib_status()

    # ------------------------------------------------------------------
    # 面板：AI 指令
    # ------------------------------------------------------------------
    def _build_prompt_panel(self, parent):
        self._panel_title(parent, "AI 指令（Prompt）")
        self._caption(parent, "每次发题时拼在题目之前，告诉外部 AI 如何作答",
                      padx=12, pady=(0, 10))

        box, self.prompt_text = self._make_text(parent, font=ui_font(10),
                                                wrap="word", bg=CARD, fg=TEXT,
                                                insertbackground=TEXT,
                                                height=10, padx=10, pady=8)
        box.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        self.prompt_text.bind("<KeyRelease>", lambda e: self._save_params())

    # ------------------------------------------------------------------
    # 面板：延时参数
    # ------------------------------------------------------------------
    def _build_param_panel(self, parent):
        self._panel_title(parent, "延时参数")
        self._caption(parent, "修改后自动保存", padx=12, pady=(0, 12))

        grid = tk.Frame(parent, bg=CARD)
        grid.pack(fill="x", padx=12)

        def field(row, col, label, attr):
            tk.Label(grid, text=label, bg=CARD, fg=TEXT,
                     font=ui_font(10)).grid(row=row, column=col * 2,
                                            sticky="e", padx=(0, 8), pady=6)
            entry = self._entry(grid)
            entry.grid(row=row, column=col * 2 + 1, sticky="w",
                       padx=(0, 24 if col == 0 else 0), pady=6)
            setattr(self, attr, entry)

        field(0, 0, "AI回答等待(ms)", "delay_ai_wait")
        field(0, 1, "点击后响应等待(ms)", "delay_click_response")
        field(1, 0, "操作间微延迟(ms)", "delay_step")
        field(1, 1, "滚动后延时(ms)", "delay_scroll_settle")
        field(2, 0, "答题数量上限", "question_limit")

        self.save_shots_var = tk.BooleanVar(value=False)
        RoundCheck(parent, "保存每题判分截图", self.save_shots_var,
                   command=self._save_params).pack(anchor="w", padx=10,
                                                   pady=(12, 0))

        self._caption(parent, "· 操作间微延迟：每步点击之间的缓冲\n"
                              "· 滚动后延时：自动滚动全部结束后，等此时长再 OCR 识别答案\n"
                              "· 判分截图存到以启动时间命名的文件夹",
                      padx=12, pady=(10, 0))

    # ==================================================================
    # 右列下半：勿扰提示 + 并排按钮 + 日志窗口
    # ==================================================================
    def _build_bottom_ui(self, parent):
        # --- 勿扰提示：胶囊标签（加粗，不用彩色逐字着色） ---
        pill = RoundCard(parent, radius=12, fill=PILL_BG, outline=PILL_BORDER,
                         pad=4, bg=BG)
        pill.pack(pady=(0, 8))
        tk.Label(pill.body, text="建议打开勿扰模式，防止微信等 APP 通知干扰",
                 bg=PILL_BG, fg=INK, font=ui_font(10, "bold")).pack(
            padx=12, pady=5)

        # --- 运行控制：两个按钮并排，节省纵向空间 ---
        ctrl = RoundCard(parent, radius=14, pad=8, bg=BG)
        ctrl.pack(fill="x")
        row = tk.Frame(ctrl.body, bg=CARD)
        row.pack(fill="x")

        self.btn_run = RoundButton(row, "启动全自动任务", self.start_automation,
                                   height=44, radius=10, bg=CARD, fill=ACCENT,
                                   fg="white", outline=ACCENT,
                                   hover=ACCENT_DARK,
                                   size=12, bold=True)
        self.btn_run.pack(side="left", fill="x", expand=True, padx=(0, 5))

        RoundButton(row, "紧急停止 (Esc)", self.stop_task, height=44, radius=10,
                    bg=CARD, fill=CARD, fg=INK, outline=BORDER,
                    hover=ACCENT_SOFT, size=12).pack(side="left", fill="x",
                                                    expand=True, padx=(5, 0))

        # --- 日志窗口（保留内容区，不显示“运行日志”标题） ---
        log_box = tk.Frame(parent, bg=BG)
        log_box.pack(fill="both", expand=True, pady=(8, 0))
        box, self.log_area = self._make_text(log_box, font=(MONO, 9),
                                             bg=CARD, fg=TEXT,
                                             insertbackground=TEXT,
                                             spacing1=2, spacing3=2,
                                             padx=10, pady=8)
        box.pack(fill="both", expand=True)
        self.log_area.tag_configure("ts", foreground=MUTED)
        # 折行缩进到正文列：lmargin2 是 tag 属性（控件没这个选项），所以给
        # 每整行挂一个 log tag；否则折行会顶到时间戳下面，看着像串行
        self.log_area.tag_configure(
            "log", lmargin2=tkfont.Font(font=(MONO, 9)).measure("00:00:00  "))
