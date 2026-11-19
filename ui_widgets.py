"""圆角控件与 UI 字体解析。

Tk 原生控件不支持圆角，这里用 Canvas 自绘：圆角背景由 PIL 生成带透明
通道的图片（3 倍超采样后缩回，边缘平滑），再作为 Canvas 背景图，前景
再叠加文字或原生输入控件。

所有组件都保持与原生控件兼容的读写接口（get/insert/current/["values"]
…），业务层无需改动。字族按偏好顺序挑选，缺失则回退，统一由
:func:`ui_font` 提供。
"""

import tkinter as tk
from functools import lru_cache
from tkinter import font as tkfont
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

# ======================================================================
# 主题调色板：一套主题 = 一整份配色，换主题后重建控件即可整页变色
# ======================================================================
# 中性色与强调色要同向（都偏暖或都偏冷），否则两种灰混在一起会显脏；
# 强颜色只用在选中态/主按钮/对勾上，其余全部落在中性灰阶上。
THEMES = {
    "taupe": {                      # 暖驼棕（默认）
        "name": "暖驼棕",
        "bg": "#F6F5F2", "card": "#FFFFFF", "border": "#E7E4DE",
        "text": "#28241F", "ink": "#514941", "muted": "#8E877D",
        "accent": "#8A6A4B", "accent_dark": "#6E5238",
        "accent_soft": "#F2ECE4",
        "pill_bg": "#F2EEE8", "pill_border": "#E8E3DA",
        "check_border": "#CCC5B9", "field_border": "#D8D2C8",
        "thumb": "#D7D4CD", "thumb_hover": "#BEB9AE",
    },
    "ink": {                        # 深墨蓝（冷调，最接近工具类软件）
        "name": "深墨蓝",
        "bg": "#F5F6F7", "card": "#FFFFFF", "border": "#E3E6E9",
        "text": "#20262B", "ink": "#4B545C", "muted": "#889097",
        "accent": "#3B4A5A", "accent_dark": "#2B3743",
        "accent_soft": "#ECEFF2",
        "pill_bg": "#EFF1F3", "pill_border": "#E4E7EA",
        "check_border": "#C8CDD2", "field_border": "#D5DBE0",
        "thumb": "#D4D8DB", "thumb_hover": "#B9BFC4",
    },
    "graphite": {                   # 石墨炭黑（最冷峻，近 Notion）
        "name": "石墨炭黑",
        "bg": "#F5F5F4", "card": "#FFFFFF", "border": "#E4E4E2",
        "text": "#1F1F1E", "ink": "#4A4A48", "muted": "#8B8B87",
        "accent": "#2F3437", "accent_dark": "#1C2022",
        "accent_soft": "#ECECEA",
        "pill_bg": "#EFEFED", "pill_border": "#E3E3E0",
        "check_border": "#C6C6C2", "field_border": "#D3D3CF",
        "thumb": "#D2D2CE", "thumb_hover": "#B6B6B1",
    },
    "green": {                      # 深墨绿（沉静）
        "name": "深墨绿",
        "bg": "#F4F6F3", "card": "#FFFFFF", "border": "#E2E7E1",
        "text": "#1E2521", "ink": "#48544C", "muted": "#879389",
        "accent": "#3F6153", "accent_dark": "#2F4B3F",
        "accent_soft": "#EAEFEC",
        "pill_bg": "#EDF1ED", "pill_border": "#E1E7E2",
        "check_border": "#C3CCC5", "field_border": "#D2DBD4",
        "thumb": "#D0D8D2", "thumb_hover": "#B4BFB7",
    },
}
THEME_ORDER = ("taupe", "ink", "graphite", "green")
DEFAULT_THEME = "taupe"

# 主题字段 -> 模块常量名（控件默认参数在运行时读这些常量）
_PALETTE_MAP = {
    "bg": "BG", "card": "CARD", "border": "BORDER", "text": "TEXT",
    "ink": "INK", "muted": "MUTED", "accent": "ACCENT",
    "accent_dark": "ACCENT_DARK", "accent_soft": "ACCENT_SOFT",
    "pill_bg": "PILL_BG", "pill_border": "PILL_BORDER",
    "check_border": "CHECK_BORDER", "field_border": "FIELD_BORDER",
    "thumb": "THUMB", "thumb_hover": "THUMB_HOVER",
}
PALETTE_NAMES = tuple(_PALETTE_MAP.values())
current_theme = DEFAULT_THEME


def apply_theme(key):
    """把某套主题写进模块常量，返回真正生效的主题键。

    控件颜色都是构造时从这些常量取的，所以换主题后必须重建控件；
    单独修改已存在的控件颜色需要逐个 set_state，不划算。
    """
    global current_theme
    current_theme = key if key in THEMES else DEFAULT_THEME
    for field, const in _PALETTE_MAP.items():
        globals()[const] = THEMES[current_theme][field]
    cache_holder = globals().get("_rounded_photo")
    if cache_holder is not None:
        cache_holder.cache_clear()   # 旧配色的位图不再命中
    return current_theme

MONO = "Consolas"       # 日志等宽字体（不随 UI 字体变化）

# 模块导入时落地默认主题：其它模块 import 到的常量就是默认配色的值。
# 换主题时调用 apply_theme()，然后重建控件（默认参数在运行时才读这些名字）。
apply_theme(DEFAULT_THEME)

# 字体候选：优先思源黑体（Noto Sans SC），其次雅黑 UI
_FONT_CANDIDATES = ("Noto Sans SC", "Microsoft YaHei UI",
                    "Microsoft YaHei", "Segoe UI")
_family = None

_SUPERSAMPLE = 3        # 圆角图片超采样倍数（抗锯齿）


def resolve_font(root):
    """挑一个本机存在的 UI 字体，返回字族名。"""
    global _family
    have = set(tkfont.families(root))
    for name in _FONT_CANDIDATES:
        if name in have:
            _family = name
            break
    else:
        _family = "Microsoft YaHei"
    # 新 Tk 实例下旧 PhotoImage 已随旧 root 失效
    _rounded_photo.cache_clear()
    return _family


def ui_font(size, weight="normal"):
    """UI 字体元组，如 :func:`ui_font` (11, "bold")。"""
    return (_family or _FONT_CANDIDATES[-1], size, weight)


def _measure(text, size, weight="normal"):
    return tkfont.Font(family=ui_font(size)[0], size=size,
                       weight=weight).measure(text)


@lru_cache(maxsize=192)
def _rounded_photo(w, h, radius, fill, outline, border):
    """生成圆角矩形背景图（四角透明，用于叠在 Canvas 上）。"""
    s = _SUPERSAMPLE
    img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = max(0, min(radius, w // 2, h // 2)) * s
    draw.rounded_rectangle((0, 0, w * s - 1, h * s - 1), radius=r,
                           fill=fill, outline=outline,
                           width=max(1, border * s))
    return ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))


def _draw_rounded(canvas, w, h, radius, fill, outline, border=1):
    """在 Canvas 上画一张圆角背景（返回图对象，调用方需保活）。"""
    photo = _rounded_photo(int(w), int(h), int(radius), fill, outline,
                           int(border))
    canvas.create_image(0, 0, anchor="nw", image=photo)
    return photo


def _blend(widget, c_from, c_to, t):
    """两个颜色按 t 线性插值，返回 #RRGGBB。

    颜色名（如 "white"）交给 Tk 自己解析，不自己维护色名表。
    """
    if t <= 0:
        return c_from
    if t >= 1:
        return c_to
    r1, g1, b1 = (v >> 8 for v in widget.winfo_rgb(c_from))
    r2, g2, b2 = (v >> 8 for v in widget.winfo_rgb(c_to))
    return "#%02x%02x%02x" % (int(r1 + (r2 - r1) * t),
                              int(g1 + (g2 - g1) * t),
                              int(b1 + (b2 - b1) * t))


# ======================================================================
# 细滚动条：无箭头、圆角滑块，配合 Text/Listbox 的 yscrollcommand
# ======================================================================
class SlimScrollbar(tk.Canvas):
    def __init__(self, parent, *, command=None, width=10, bg=None,
                 thumb=None, hover=None, radius=4):
        bg = CARD if bg is None else bg
        thumb = THUMB if thumb is None else thumb
        hover = THUMB_HOVER if hover is None else hover
        super().__init__(parent, width=width, bg=bg, bd=0,
                         highlightthickness=0)
        self._command = command
        self._thumb = thumb
        self._hover_color = hover
        self._cur = thumb
        self._radius = radius
        self._first = 0.0
        self._last = 1.0
        self._grab = None
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", lambda e: self._set_color(hover))
        self.bind("<Leave>", lambda e: self._set_color(thumb))
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set(self, first, last):
        """供 Text 的 yscrollcommand 调用。"""
        self._first = float(first)
        self._last = float(last)
        self._redraw()

    # -- 内部 ------------------------------------------------------
    def _set_color(self, color):
        self._cur = color
        self._redraw()

    def _span(self):
        return max(1e-6, min(1.0, self._last - self._first))

    def _thumb_box(self):
        h = self.winfo_height()
        if h <= 8:
            return None
        span = self._span()
        th = min(h, max(24.0, span * h))
        top = 0.0 if span >= 1.0 else (self._first / (1 - span)) * (h - th)
        return max(0.0, min(h - th, top)), th

    def _redraw(self):
        self.delete("all")
        # 内容不足一屏时不画滑块（日志少时保持干净）
        if self._span() >= 0.999:
            return
        box = self._thumb_box()
        if not box:
            return
        top, th = box
        w = self.winfo_width()
        # 自己持住图对象：LRU 缓存被挤掉后图不应跟着失效
        self._photo = _rounded_photo(max(1, w - 4), max(4, int(th)),
                                     self._radius, self._cur, self._cur, 0)
        self.create_image(2, top, anchor="nw", image=self._photo)

    def _on_press(self, event):
        box = self._thumb_box()
        if not box:
            return
        top, th = box
        if top <= event.y <= top + th:
            self._grab = event.y - top
        else:
            self._grab = th / 2
            self._moveto(event.y - self._grab)

    def _on_drag(self, event):
        if self._grab is not None:
            self._moveto(event.y - self._grab)

    def _on_release(self, _event=None):
        self._grab = None

    def _moveto(self, top):
        h = self.winfo_height()
        span = self._span()
        box = self._thumb_box()
        if not box or not self._command:
            return
        th = box[1]
        room = max(1.0, h - th)
        frac = (top / room) * (1 - span)
        self._command("moveto", max(0.0, min(1.0, frac)))


# ======================================================================
# 色板小方块：外圈表示选中态，内部是主题强调色
# ======================================================================
class ColorSwatch(tk.Canvas):
    def __init__(self, parent, color, command=None, *, size=20, box=13,
                 radius=4, bg=None):
        bg = BG if bg is None else bg
        super().__init__(parent, width=size, height=size, bg=bg, bd=0,
                         highlightthickness=0, cursor="hand2")
        self._color = color
        self._command = command
        self._size = size
        self._box = box
        self._radius = radius
        self._selected = False
        self.bind("<Button-1>", self._on_click)
        self._redraw()

    def set_selected(self, on):
        self._selected = bool(on)
        self._redraw()

    def _on_click(self, _event=None):
        if self._command:
            self._command()

    def _redraw(self):
        self.delete("all")
        s, b = self._size, self._box
        # 选中态：外面套一圈同色细环，与色块之间留空隙才看得出是“选中的那块”
        if self._selected:
            self._ring = _rounded_photo(s, s, self._radius + 4, None,
                                        self._color, 1)
            self.create_image(0, 0, anchor="nw", image=self._ring)
        off = (s - b) // 2
        self._photo = _rounded_photo(b, b, self._radius, self._color,
                                     self._color, 1)
        self.create_image(off, off, anchor="nw", image=self._photo)


# ======================================================================
# 圆角卡片：Canvas 铺满画背景（创建早于 body，处于下层），body 正常 pack
# 并内缩 pad，因此卡片请求尺寸由内容决定——外部不拉伸时高度自适应内容
# ======================================================================
class RoundCard(tk.Frame):
    def __init__(self, parent, *, radius=14, fill=None, outline=None,
                 pad=10, bg=None, **kw):
        fill = CARD if fill is None else fill
        outline = BORDER if outline is None else outline
        bg = BG if bg is None else bg
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0, **kw)
        self._radius = radius
        self._fill = fill
        self._outline = outline
        self._bg = bg
        self._pad = pad
        # Canvas 默认请求尺寸是 10c×7c（约 378×265），用 place 且给定 1x1
        # 才不会凭空撑宽所在的 grid 列
        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0,
                                width=1, height=1)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True, padx=pad, pady=pad)
        self._size = (0, 0)
        self.canvas.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 4 or h < 4:
            return
        self.canvas.delete("card_bg")
        self._photo = _draw_rounded(self.canvas, w, h, self._radius, self._fill,
                                    self._outline)
        self.canvas.tag_lower("card_bg")


# ======================================================================
# 圆角按钮
# ======================================================================
class RoundButton(tk.Canvas):
    # 颜色过渡：hover 与选中态切换时在 FADE_MS 内插值，避免颜色硬跳
    FADE_MS = 110
    FADE_STEP_MS = 15

    def __init__(self, parent, text="", command=None, *, width=None, height=34,
                 radius=9, bg=None, fill=None, fg=None, outline=None,
                 hover=None, size=10, bold=False, wrap=0):
        bg = BG if bg is None else bg
        fill = CARD if fill is None else fill
        fg = TEXT if fg is None else fg
        outline = BORDER if outline is None else outline
        hover = ACCENT_SOFT if hover is None else hover
        super().__init__(parent, bg=bg, height=height, highlightthickness=0,
                         bd=0, cursor="hand2")
        self._text = text
        self._command = command
        self._radius = radius
        self._fill = fill
        self._fg = fg
        self._outline = outline
        self._hover = hover
        self._size = size
        self._bold = bold
        self._wrap = wrap
        self._cur_fill = fill
        self._enabled = True
        # 实际画在画布上的颜色（过渡中的中间值），目标值变了就往它渐变
        self._shown = (fill, fg, outline)
        self._fade = (fill, fg, outline)     # 过渡起点
        self._fade_target = (fill, fg, outline)
        self._fade_t = 1.0
        self._fade_job = None
        if width is None:
            width = _measure(text, size, "bold" if bold else "normal") + 36
        self.configure(width=max(4, int(width)))
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Destroy>", self._on_destroy)

    # -- 对外接口 --------------------------------------------------
    def set_text(self, text):
        self._text = text
        self._redraw()

    def set_state(self, *, fill=None, fg=None, outline=None, hover=None,
                  bold=None):
        """切换配色/字重（标签选中态、置顶态用它）。"""
        old_bold = self._bold
        if fill is not None:
            self._fill = fill
            self._cur_fill = fill
        if fg is not None:
            self._fg = fg
        if outline is not None:
            self._outline = outline
        if hover is not None:
            self._hover = hover
        if bold is not None:
            self._bold = bold
        # 只有字重变了（颜色目标没变）就没有过渡回调来重画，得自己补一次
        if not self._fade_to_target() and self._bold != old_bold:
            self._redraw()

    def set_enabled(self, on):
        self._enabled = bool(on)
        self.configure(cursor="hand2" if on else "arrow")
        self._redraw()

    # -- 内部 ------------------------------------------------------
    def _on_click(self, _event=None):
        if self._enabled and self._command:
            self._command()

    def _on_enter(self, _event=None):
        if self._enabled:
            self._cur_fill = self._hover
            self._fade_to_target()

    def _on_leave(self, _event=None):
        self._cur_fill = self._fill
        self._fade_to_target()

    def _on_destroy(self, _event=None):
        """控件销毁时停掉过渡，否则 after 会落到已失效的控件上。"""
        if self._fade_job is not None:
            try:
                self.after_cancel(self._fade_job)
            except tk.TclError:
                pass
            self._fade_job = None

    def _fade_to_target(self):
        """向目标色过渡；目标没变则不动（动画中途仍朝同一目标时不重播）。

        返回是否有过渡在推进（推进中每帧都会重画，调用方不必再补画）。
        """
        target = (self._cur_fill, self._fg, self._outline)
        if target == self._fade_target:
            return self._fade_job is not None
        self._fade = self._shown          # 从当前中间色继续，反向也平滑
        self._fade_target = target
        self._fade_t = 0.0
        if self._fade_job is None:
            self._fade_step()
        return self._fade_job is not None

    def _fade_step(self):
        self._fade_t = min(1.0, self._fade_t + self.FADE_STEP_MS / self.FADE_MS)
        p = 1 - (1 - self._fade_t) ** 2          # ease-out：起步快、收尾缓
        self._shown = tuple(_blend(self, a, b, p)
                            for a, b in zip(self._fade, self._fade_target))
        self._redraw()
        if self._fade_t < 1.0:
            self._fade_job = self.after(self.FADE_STEP_MS, self._fade_step)
        else:
            self._fade_job = None
            self._shown = self._fade_target

    def _redraw(self):
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        fill, fg, outline = self._shown
        self.delete("all")
        self._photo = _draw_rounded(self, w, h, self._radius, fill, outline)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg,
                         width=self._wrap or 0, justify="center",
                         font=ui_font(self._size,
                                      "bold" if self._bold else "normal"))


# ======================================================================
# 对勾式复选框：圆角小方框 + 手绘对号（避免 ttk 方块勾选态的歧义）
# ======================================================================
class RoundCheck(tk.Canvas):
    def __init__(self, parent, text, variable, command=None, *, bg=None,
                 size=10, box=19, radius=5):
        bg = CARD if bg is None else bg
        height = box + 10
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0,
                         cursor="hand2", height=height,
                         width=box + 12 + _measure(text, size))
        self.var = variable
        self._command = command
        self._text = text
        self._bg = bg
        self._size = size
        self._box = box
        self._radius = radius
        self._height = height
        self._hot = False
        variable.trace_add("write", lambda *a: self._redraw())
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda e: self._set_hot(True))
        self.bind("<Leave>", lambda e: self._set_hot(False))
        self._redraw()

    def _set_hot(self, hot):
        self._hot = hot
        self._redraw()

    def _toggle(self, _event=None):
        self.var.set(not bool(self.var.get()))
        if self._command:
            self._command()

    def _redraw(self):
        h = self._box
        x0, y0 = 2.0, (self._height - h) / 2
        checked = bool(self.var.get())
        self.delete("all")
        self._photo = _rounded_photo(h, h, self._radius,
                                     ACCENT if checked else self._bg,
                                     ACCENT if (checked or self._hot)
                                     else CHECK_BORDER, 1)
        self.create_image(x0, y0, anchor="nw", image=self._photo)
        if checked:
            self.create_line(x0 + h * 0.26, y0 + h * 0.52,
                             x0 + h * 0.44, y0 + h * 0.70,
                             x0 + h * 0.76, y0 + h * 0.30,
                             fill="white", width=2,
                             capstyle="round", joinstyle="round")
        self.create_text(x0 + h + 6, self._height / 2, text=self._text,
                         anchor="w", fill=TEXT, font=ui_font(self._size))


# ======================================================================
# 圆角输入框：外层 Canvas 画边框，内部同色 Entry（同色则直角不可见）
# ======================================================================
class RoundEntry(tk.Frame):
    def __init__(self, parent, *, width=96, height=32, radius=9, bg=None,
                 fill=None, size=10, justify="center"):
        bg = CARD if bg is None else bg
        fill = CARD if fill is None else fill
        super().__init__(parent, bg=bg, width=width, height=height, bd=0,
                         highlightthickness=0)
        self.pack_propagate(False)
        self._radius = radius
        self._bg = bg
        self._fill = fill
        self._focused = False
        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0,
                                width=1, height=1)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.entry = tk.Entry(self, relief="flat", bd=0, highlightthickness=0,
                              bg=fill, fg=TEXT, insertbackground=TEXT,
                              justify=justify, font=ui_font(size))
        self.entry.place(x=10, y=2, relwidth=1, width=-20, relheight=1,
                         height=-4)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.entry.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._set_focus(False))

    def _set_focus(self, on):
        self._focused = on
        self._redraw()

    def _redraw(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 4 or h < 4:
            return
        self.canvas.delete("all")
        self._photo = _draw_rounded(self.canvas, w, h, self._radius, self._fill,
                                    ACCENT if self._focused else FIELD_BORDER)

    def __getattr__(self, name):
        entry = self.__dict__.get("entry")
        if entry is not None:
            return getattr(entry, name)
        raise AttributeError(f"{type(self).__name__!r} has no {name!r}")


# ======================================================================
# 圆角下拉框：外层 Canvas 画边框，内部 Combobox 边框染成同色
# ======================================================================
class RoundCombo(tk.Frame):
    def __init__(self, parent, *, height=32, radius=9, bg=None, fill=None,
                 size=10):
        bg = CARD if bg is None else bg
        fill = CARD if fill is None else fill
        super().__init__(parent, bg=bg, height=height, bd=0,
                         highlightthickness=0)
        self.pack_propagate(False)
        self._radius = radius
        self._bg = bg
        self._fill = fill
        style = ttk.Style(self)
        style.configure("Card.TCombobox", fieldbackground=fill, background=fill,
                        foreground=TEXT, bordercolor=fill, lightcolor=fill,
                        darkcolor=fill, arrowcolor=MUTED, padding=(4, 3),
                        relief="flat")
        # 必须把每个状态的前/背景都写死：ttk 自带的 map 在
        # 「readonly + focus」下会给一个白色前景，而底色也是白，
        # 于是光标一进下拉框文字就消失了（看着像值被清空）。
        style.map("Card.TCombobox",
                  foreground=[("readonly", "focus", TEXT), ("readonly", TEXT),
                              ("focus", TEXT), ("disabled", MUTED)],
                  fieldforeground=[("readonly", "focus", TEXT),
                                   ("readonly", TEXT), ("focus", TEXT),
                                   ("disabled", MUTED)],
                  fieldbackground=[("readonly", "focus", fill),
                                   ("readonly", fill), ("focus", fill)],
                  background=[("readonly", fill), ("focus", fill),
                              ("active", fill)],
                  selectbackground=[("readonly", fill), ("focus", fill)],
                  selectforeground=[("readonly", TEXT), ("focus", TEXT)],
                  arrowcolor=[("disabled", MUTED), ("active", MUTED)],
                  bordercolor=[("readonly", fill), ("focus", fill)])
        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0,
                                width=1, height=1)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        # 自己持一个 StringVar：ttk 内部变量的内容会在弹窗开关、
        # -values 重设时被清掉，外部变量不会，显示值因此不会凭空变空
        self.var = tk.StringVar()
        self.combo = ttk.Combobox(self, state="readonly",
                                  style="Card.TCombobox", font=ui_font(size),
                                  textvariable=self.var)
        self.combo.place(x=8, y=2, relwidth=1, width=-16, relheight=1,
                         height=-4)
        self.canvas.bind("<Configure>", lambda e: self._redraw())

    def _redraw(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 4 or h < 4:
            return
        self.canvas.delete("all")
        self._photo = _draw_rounded(self.canvas, w, h, self._radius, self._fill,
                                    FIELD_BORDER)

    def __getitem__(self, key):
        return self.combo[key]

    def __setitem__(self, key, value):
        self.combo[key] = value

    def __getattr__(self, name):
        combo = self.__dict__.get("combo")
        if combo is not None:
            return getattr(combo, name)
        raise AttributeError(f"{type(self).__name__!r} has no {name!r}")
