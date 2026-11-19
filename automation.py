"""自动化主流程：状态机主循环、答题页处理、长题滚动采集与提交。

用到的"看图"能力来自 vision.py，"问 AI"能力来自 ai_bridge.py，本模块只
负责把它们串成流程并操作手机。
"""

import time

from adb_service import AdbError
from app_state import (STATE_GROUP_END, STATE_QUESTION, STATE_RESULT,
                       STATE_SUBMITTED, STATE_UNKNOWN)
from config import (ADB_TIMEOUT, MAX_CONSECUTIVE_FAILURES,
                    MAX_LONG_Q_SCROLLS, SCROLL_SETTLE_S, WORD_FINISH)
from layout_profile import DEFAULT_LAYOUT


class AutomationMixin:
    """状态机：截图 → 判状态 → 答题/提交/翻页。"""

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
            top_y = frame.shape[0] * DEFAULT_LAYOUT.button_top_ratio
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
    # 答题页处理
    # ------------------------------------------------------------------
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
    def _scroll_page(self, frame_w, frame_h, reverse=False):
        """滑动答题页。默认上滑使页面下滚（露出下方内容），reverse 反向。"""
        layout = DEFAULT_LAYOUT
        x = frame_w / 2
        y_from = frame_h * (layout.scroll_to_ratio if reverse
                            else layout.scroll_from_ratio)
        y_to = frame_h * (layout.scroll_from_ratio if reverse
                          else layout.scroll_to_ratio)
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
