"""外部 AI 窗口交互（PC 侧）：发送题目、滚动到页末、提纯答案。

全部走剪贴板 + pyautogui + 屏幕截图 OCR，与手机无关，因此与状态机分离。
"""

import re
import time

import mss
import numpy as np
import pyautogui
import pyperclip

from app_state import (AI_PAGE_ANSWER_WORD_RE, AI_PAGE_LETTER_RE,
                       AI_PAGE_OPTION_RE)
from config import (AI_ANSWER_POLL_S, AI_ANSWER_RETRY_S, AI_SCROLL_CLICKS,
                    AI_SCROLL_UNITS)
from ocr_engine import get_ocr


class AiBridgeMixin:
    """通过屏幕坐标操作外部 AI 网页：发题、滚动、读答案。"""

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
