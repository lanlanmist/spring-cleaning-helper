"""外部 AI 窗口标定（仅内存，关闭即清除）。

标定只涉及屏幕坐标与鼠标框选，与识别流程无关，单独成模块便于查改。
"""

from gui_selectors import PointSelector, RegionSelector


class CalibrationMixin:
    """AI 页面区 / 输入框 / 发送键 / 滚动位置的框选与状态展示。"""

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
