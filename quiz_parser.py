"""题目页面解析：从全屏 OCR 结果中提取题干与选项点击坐标。

核心策略（几何推断，应对水印干扰 / 选项内容含 ABCD 字母）：
1. 过滤水印/状态栏噪声行；
2. 按 y 坐标把文本框聚类为"视觉行"（字母与文字可能被拆成同行的多个框）；
3. 以"题目"标记行为锚点向下扩展得到题干；
4. 选项定位 = 几何特征而非文本前缀：
   - 选项前缀框（"A."/"B."）位于题型标签正下方同一竖带内；
   - 选项行纵向等间距（行距取众数），用已识别前缀行线性拟合出 A 的行位置；
   - 缺失前缀的选项行（如 A. 被水印污染）按拟合槽位回填；
   - 其余内容行（含选项文本里出现的 A/B/C/D 字母）就近归属，不当作选项；
5. 选项点击坐标取 (屏幕宽/2, 行中心y)，选项卡片横向铺满，命中稳定。
"""

import re

# 宽松前缀：行首 "A." 后可能跟选项内容（前缀与内容同框）；
# OCR 常把 "." 误识为 ":"，一并容忍
OPTION_PREFIX_RE = re.compile(r'^\s*([A-H])\s*[.。:：]')
# 严格前缀：整个框只是 "A."（前缀与内容分框）
OPTION_PREFIX_STRICT_RE = re.compile(r'^\s*([A-H])\s*[.。:：]\s*$')
# 水印特征：完整日期 2026-08-04、5 位以上数字串、残缺日期片段 08-04 21:29
WATERMARK_RE = re.compile(
    r'(20\d{2}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}'
    r'|\b\d{5,}\b'
    r'|^\s*\d{1,2}\s*[-./]\s*\d{1,2}\s+\d{1,2}[:：]\d{2})')

QUESTION_MARKER = "题目"
HINT_WORD = "查看提示"
TYPE_WORDS = ("单选题", "多选题", "选择题", "判断题")
# 选项文本开头混入的水印数字片段（如 "D.22将循环..." 中的 22、"A:8- ..."）
OPTION_LEAD_DIGITS_RE = re.compile(
    r'^(?:\d{1,4}|[A-H])[:：]?\s*\d{0,2}\s*[-./]\s*')
# 代码块语言标签（"sql"/"python" 等独立成框）与"复制"按钮
CODE_LANG_TAGS = ("sql", "python", "java", "javascript", "c++", "shell", "go")
CODE_COPY_WORD = "复制"
# 行首行号（代码块 "1 SELECT ..." 中的 1）
LINE_NO_RE = re.compile(r'^\s*\d{1,3}\s+(?=\S)')


def is_noise(text):
    """判断一行文本是否为水印/状态栏噪声。

    注意：纯数字/符号行不再整体过滤——选项可能是 "25, 0" 这类
    纯数字内容；水印主体已在 OCR 前做颜色抹除，这里只拦日期模式。
    """
    t = text.strip()
    if not t:
        return True
    if WATERMARK_RE.search(t):
        return True
    return False


def group_rows(ocr_lines, gap_threshold, band_right=0.0):
    """把 [(box, text), ...] 按 y 中心聚类为视觉行。

    返回 [{'y','bottom','x0','x1','text'}, ...]，按 y 升序。
    行内多个框按 x 排序合并（如 "B." 与 "错误" 合并为 "B. 错误"）。
    位于选项前缀竖带（x0 <= band_right）内的框独立成行，
    避免水印/前缀与同行内容框误合并。
    """
    items = []
    for box, text in ocr_lines:
        cy = (box[0][1] + box[2][1]) / 2
        bottom = max(p[1] for p in box)
        x0 = min(p[0] for p in box)
        x1 = max(p[0] for p in box)
        # band 标记位：True 表示该框属于前缀竖带，聚类时不与内容框合并
        items.append((cy, bottom, x0, x1, text.strip(),
                      band_right > 0 and x0 <= band_right))
    items.sort(key=lambda t: t[0])

    rows = []
    for cy, bottom, x0, x1, text, in_band in items:
        last = rows[-1] if rows else None
        mergeable = (last is not None and abs(cy - last["y"]) <= gap_threshold
                     and last["_band"] == in_band)
        if mergeable:
            row = last
            n = len(row["_parts"])
            row["y"] = (row["y"] * n + cy) / (n + 1)
            row["bottom"] = max(row["bottom"], bottom)
            row["x0"] = min(row["x0"], x0)
            row["x1"] = max(row["x1"], x1)
            row["_parts"].append((x0, text))
        else:
            rows.append({"y": cy, "bottom": bottom, "x0": x0, "x1": x1,
                         "_band": in_band, "_parts": [(x0, text)]})
    for row in rows:
        parts = sorted(row.pop("_parts"), key=lambda p: p[0])
        row["text"] = " ".join(t for _, t in parts)
    return rows


def _clean_option_text(text):
    """去掉选项前缀残留与开头混入的水印数字。"""
    text = OPTION_PREFIX_RE.sub("", text, count=1).strip()
    return OPTION_LEAD_DIGITS_RE.sub("", text)


def _merge_visual_lines(rows, threshold):
    """把行按 y 再次聚类为视觉行，行内按 x 排序（代码块多列框保序）。"""
    merged = []
    for r in sorted(rows, key=lambda r: r["y"]):
        if merged and abs(r["y"] - merged[-1][0]["y"]) <= threshold:
            merged[-1].append(r)
        else:
            merged.append([r])
    return [" ".join(r["text"] for r in sorted(part, key=lambda r: r["x0"]))
            for part in merged]


def _clean_qline(text):
    """清洗题目区的一行文本；应整体丢弃时返回 None。"""
    t = text.strip()
    if not t:
        return None
    # 代码块 UI：语言标签、复制按钮（可能同行出现）
    tokens = [tk.strip("。， ") for tk in t.split()]
    if all(tk.lower() in CODE_LANG_TAGS or tk == CODE_COPY_WORD
           for tk in tokens if tk):
        return None
    # 孤立行号（代码块左侧的 1/2/3）
    if re.fullmatch(r'\d{1,3}', t):
        return None
    # 行首行号（"1 SELECT ..."）与"题目"标记
    t = LINE_NO_RE.sub("", t)
    t = re.sub(r'[【\[（(]?\s*题目\s*[:：]?\s*[】\])）]?', '', t)
    t = t.replace(HINT_WORD, "").strip()
    return t or None


def _is_option_like(r, band_right):
    """竖带内且形如 "X." 前缀的行（选项起点）。"""
    text = r["text"].strip()
    return (r["x0"] <= band_right
            and re.match(r'^\s*[A-H]\s*[.。:：]', text) is not None)


def _sequential_options(zone, frame_w):
    """兜底：无任何前缀框时，按视觉行顺序依次赋 A/B/C...。"""
    options = {}
    last_idx = -1
    for seq, r in enumerate(zone):
        m = OPTION_PREFIX_RE.match(r["text"])
        if m:
            idx = ord(m.group(1)) - ord("A")
        elif last_idx + 1 < len(zone):
            idx = last_idx + 1
        else:
            idx = seq
        letter = chr(ord("A") + idx)
        last_idx = max(last_idx, idx)
        if letter not in options:
            options[letter] = {"text": _clean_option_text(r["text"]),
                               "pos": (frame_w / 2, r["y"])}
    return options


def parse_question_page(ocr_lines, frame_w, frame_h):
    """解析答题页。

    返回 (question_text, options)：
      question_text: 题干文本（含题型标签），失败为 None；
      options: {字母: {"text": 选项文本, "pos": (点击x, 点击y)}}。
    """
    clean = [(box, t) for box, t in ocr_lines if not is_noise(t)]
    # 选项前缀竖带：屏幕左侧约 19% 宽（选项字母排在题型标签正下方）
    band_right = frame_w * 0.19
    rows = group_rows(clean, frame_h * 0.02, band_right)
    if not rows:
        return None, {}

    # 题型标签（题目锚点上方的"单选题/多选题/判断题"）
    type_label = ""
    q_idx = next((i for i, r in enumerate(rows) if QUESTION_MARKER in r["text"]),
                 None)
    if q_idx is None:
        # 无"题目"标记时，取第一个足够长的行作题干起点
        q_idx = next((i for i, r in enumerate(rows)
                      if len(r["text"]) >= 8 and r["y"] > frame_h * 0.1), None)
    if q_idx is None:
        return None, {}
    for r in rows[:q_idx]:
        hit = next((w for w in TYPE_WORDS if w in r["text"]), None)
        if hit:
            type_label = hit
            break

    # --- 题目区边界：首个选项前缀行（竖带内 "X." 行）或"查看提示" ---
    hint_idx = next((i for i, r in enumerate(rows) if HINT_WORD in r["text"]),
                    len(rows))
    opt_start = next((i for i in range(q_idx + 1, hint_idx)
                      if _is_option_like(rows[i], band_right)), None)

    if opt_start is not None:
        # 与首个前缀行同一视觉行的内容框（如判断题的"正确"）在 rows 里
        # 可能因 y 坐标抖动排在前缀行之前——按 y 边界一并划入选项区，
        # 否则会误并入题干导致选项 A 为空
        y_bound = rows[opt_start]["y"] - frame_h * 0.012
        q_area_end = next((i for i in range(q_idx + 1, opt_start + 1)
                           if rows[i]["y"] >= y_bound), opt_start)
    else:
        # 兜底（无 "X." 前缀的页面，如纯判断题）：按行距扩展题干
        q_end = q_idx
        i = q_idx + 1
        while i < hint_idx:
            nxt = rows[i]
            gap = nxt["y"] - rows[q_end]["y"]
            if gap >= frame_h * 0.06 or HINT_WORD in nxt["text"]:
                break
            if nxt.get("_band") and (nxt["x1"] - nxt["x0"]) < frame_w * 0.2:
                i += 1
                continue  # 竖带内窄框（残留噪声）：跳过不并入题干
            q_end = i
            i += 1
        q_area_end = q_end + 1

    # 题目区文本：视觉行重排（代码块多列框按 x 排序）后逐行清洗
    question_lines = []
    for text in _merge_visual_lines(rows[q_idx:q_area_end],
                                    frame_h * 0.012):
        t = _clean_qline(text)
        if t:
            question_lines.append(t)
    question_text = " ".join(question_lines).strip()
    if type_label:
        question_text = f"[{type_label}] {question_text}"

    # --- 选项区：题目区边界 ~ "查看提示" ---
    if hint_idx < len(rows):
        zone = rows[q_area_end:hint_idx]
    else:
        zone = [r for r in rows[q_area_end:] if r["y"] < frame_h * 0.75]
    if not zone:
        return question_text, {}

    # --- 按几何列分类：选项前缀竖带 vs 内容列 ---
    prefix_rows = []   # {'row','letter','rest'}：带 "X." 前缀的选项行
    band_extra = []    # 竖带内非前缀行（水印/缺失前缀的选项行，仅作槽位候选）
    content_rows = []  # 选项内容行（含选项文本里拆出的孤立字母）
    for r in zone:
        text = r["text"].strip()
        if r["x0"] <= band_right:
            m_strict = OPTION_PREFIX_STRICT_RE.match(text)
            m_loose = OPTION_PREFIX_RE.match(text)
            if m_strict:
                prefix_rows.append({"row": r, "letter": m_strict.group(1),
                                    "rest": ""})
                continue
            if m_loose:
                prefix_rows.append({"row": r, "letter": m_loose.group(1),
                                    "rest": text[m_loose.end():].strip()})
                continue
            band_extra.append(r)
            continue
        content_rows.append(r)

    if not prefix_rows:
        # 完全无前缀（如纯判断题）：按顺序兜底分配
        return question_text, _sequential_options(zone, frame_w)

    # --- 行距估计：前缀行足够多时最小二乘拟合（抗漏识前缀造成的
    #     大间隔），否则取间隔众数（选项纵向等间距） ---
    ys_pairs = sorted((ord(p["letter"]) - ord("A"), p["row"]["y"])
                      for p in prefix_rows)
    if len(ys_pairs) >= 3:
        n = len(ys_pairs)
        mx = sum(x for x, _ in ys_pairs) / n
        my = sum(y for _, y in ys_pairs) / n
        den = sum((x - mx) ** 2 for x, _ in ys_pairs)
        row_gap = (sum((x - mx) * (y - my) for x, y in ys_pairs) / den
                   if den else frame_h * 0.09)
    else:
        ysv = [y for _, y in ys_pairs]
        gaps = [b - a for a, b in zip(ysv, ysv[1:])]
        if gaps:
            median = sorted(gaps)[len(gaps) // 2]
            keep = [g for g in gaps if g >= median * 0.6] or gaps
            row_gap = sum(keep) / len(keep)
        else:
            row_gap = frame_h * 0.09

    # --- 线性拟合出字母 A 的行位置，槽位数以最大前缀字母为准 ---
    origins = [p["row"]["y"] - (ord(p["letter"]) - ord("A")) * row_gap
               for p in prefix_rows]
    y_origin = sum(origins) / len(origins)
    n_slots = min(8, max(ord(p["letter"]) - ord("A") + 1
                         for p in prefix_rows))

    letter_rows = []   # [letter, y, 初始文本]
    used = set()
    for p in sorted(prefix_rows, key=lambda p: p["row"]["y"]):
        letter_rows.append([p["letter"], p["row"]["y"], p["rest"]])
        used.add(id(p["row"]))

    # 缺失字母按槽位回填：优先取槽位附近的宽内容行作为该选项行
    pool = [r for r in content_rows + band_extra if id(r) not in used]
    for k in range(n_slots):
        letter = chr(ord("A") + k)
        if any(lr[0] == letter for lr in letter_rows):
            continue
        ey = y_origin + k * row_gap
        if not (0 <= ey <= frame_h):
            continue
        cands = [r for r in pool if abs(r["y"] - ey) <= row_gap * 0.5]
        if cands:
            # 宽内容行优先（窄框多为水印残留），同级再按距离
            cands.sort(key=lambda r: (0 if (r["x1"] - r["x0"]) > frame_w * 0.3
                                      else 1, abs(r["y"] - ey)))
            r = cands[0]
            # 窄框（水印）仅借用其 y 定位，文本不带入
            wide = (r["x1"] - r["x0"]) > frame_w * 0.3
            letter_rows.append([letter, r["y"], r["text"] if wide else ""])
            used.add(id(r))
        else:
            letter_rows.append([letter, ey, ""])
    letter_rows.sort(key=lambda t: t[1])

    options = {lr[0]: {"text": lr[2], "pos": (frame_w / 2, lr[1])}
               for lr in letter_rows}

    # --- 内容行就近归属（选项内容折行/拆框都归到最近的选项行） ---
    for r in content_rows:
        if id(r) in used:
            continue
        nearest = min(letter_rows, key=lambda lr: abs(lr[1] - r["y"]))
        if abs(nearest[1] - r["y"]) <= row_gap * 0.6:
            info = options[nearest[0]]
            info["text"] = (info["text"] + " " + r["text"]).strip()

    for info in options.values():
        info["text"] = _clean_option_text(info["text"])

    return question_text, options
