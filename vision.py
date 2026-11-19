"""视觉识别层：截图、OCR、页面状态判定与题目解析。

只做"看图 → 得出结论"这一步，不操作设备、不碰 Tk 控件（日志除外）。
点击与流程控制分别在 automation.py / ai_bridge.py。
"""

import os
import re
import time

import numpy as np
from PIL import Image

from adb_service import AdbError
from app_state import (AI_PAGE_ANSWER_WORD_RE, STATE_GROUP_END,
                       STATE_QUESTION, STATE_RESULT, STATE_SUBMITTED,
                       STATE_UNKNOWN)
from config import (ADB_TIMEOUT, WORD_CORRECT_ANSWER, WORD_FINISH,
                    WORD_GROUP_END, WORD_LOADING, WORD_NEXT, WORD_SUBMIT)
from image_clean import remove_watermark
from layout_profile import DEFAULT_LAYOUT
from ocr_engine import get_ocr
from quiz_parser import (HINT_WORD, OPTION_PREFIX_RE, QUESTION_MARKER,
                         TYPE_WORDS, extract_slots_from_band,
                         parse_question_page)


class VisionMixin:
    """手机截图 OCR、页面状态判定、题干与选项解析。"""

    # ------------------------------------------------------------------
    # OCR 与状态判定
    # ------------------------------------------------------------------
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
        top_zone = frame_h * DEFAULT_LAYOUT.button_top_ratio

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
        band_w = max(1, int(frame_w * DEFAULT_LAYOUT.option_band_ratio))
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

    def _find_option_on_screen(self, letter, frame_w, frame_h):
        """当前屏 OCR 找到指定字母的选项前缀框，返回点击坐标；否则 None。"""
        _, lines = self._screenshot_ocr()
        if not lines:
            return None
        # 行首字母后可能跟 . : 、 ) 或直接结束（OCR 丢了分隔符）
        pat = re.compile(rf'^\s*{letter}\s*(?:[.。．:：、)）]|$)')
        band = frame_w * DEFAULT_LAYOUT.option_band_ratio
        cands = [box for box, text in lines if pat.match(text.strip())]
        if not cands:
            return None
        # 优先竖带内的前缀框（选项字母列）；同在竖带内取最靠上的一个，
        # 避免选项正文恰好以该字母开头时被误命中
        in_band = [box for box in cands
                   if min(p[0] for p in box) <= band]
        box = min(in_band or cands, key=lambda b: self._box_center(b)[1])
        return frame_w / 2, self._box_center(box)[1]

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

    # ------------------------------------------------------------------
    # 截图 + OCR（长题滚动采集复用）
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
