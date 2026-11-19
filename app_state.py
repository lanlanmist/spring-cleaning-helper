"""运行期共享的状态常量与参数快照。

这些定义原先写在 ocr_app.py 模块级，拆分后 vision / ai_bridge / automation
等多个模块都要引用它们。放在独立模块可避免各 mixin 之间互相 import 造成
循环依赖（各模块只依赖本模块，不依赖 ocr_app）。
"""

import re
from dataclasses import dataclass

# --- 页面状态（按优先级判定） ---
STATE_QUESTION = "question"     # 答题页（检测到选项/查看提示）
STATE_SUBMITTED = "submitted"   # 已选择，出现"确定"
STATE_RESULT = "result"         # 已判分，出现"下一题"
STATE_GROUP_END = "group_end"   # 组结束，出现"再来一组"
STATE_UNKNOWN = "unknown"

# --- AI 全页面提纯答案用的行级正则 ---
AI_PAGE_OPTION_RE = re.compile(r"^\s*[A-H]\s*[.。:：]")            # "A. xxx" 选项行
AI_PAGE_LETTER_RE = re.compile(                                    # 独立答案字母行："A"、"A,C"
    r"^\s*([A-H](?:\s*[,，、]\s*[A-H])*)\s*[.。]?\s*$")
AI_PAGE_ANSWER_WORD_RE = re.compile(                               # "答案：A" 行
    r"答案\s*[:：]?\s*([A-H](?:\s*[,，、]\s*[A-H])*)")

PARAMS_SAVE_DEBOUNCE_MS = 800   # 参数改动合并写入的延迟（毫秒）


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
