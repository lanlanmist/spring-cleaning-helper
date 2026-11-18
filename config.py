import os
import sys
import ctypes

# --- DPI 适配（必须在创建 Tk 窗口前完成） ---
try:
    ctypes.windll.user32.SetProcessDPIAware()
except (AttributeError, OSError):
    pass

# --- 屏蔽 Paddle 杂讯（必须在 import paddleocr 之前设置才生效） ---
# paddleocr 的导入与模型加载已移到 ocr_engine.get_ocr()（懒加载），
# 但环境变量仍需在这里、早于任何 paddleocr 导入就设好
os.environ.update({
    "FLAGS_use_onednn": "0",
    "FLAGS_enable_pir_api": "0",
    "PADDLE_DISABLE_PIR": "1",
})

# --- 基础目录：PyInstaller 冻结后锚定 exe 所在目录，源码运行时为项目目录 ---
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- 全局常量 ---
APP_VERSION = "大扫除小助手 V3.3"
PARAMS_FILE = os.path.join(BASE_DIR, "params.json")
ADB_DIR = os.path.join(BASE_DIR, "adb")
SHOT_DIR = os.path.join(BASE_DIR, "screenshots")
LOG_DIR = os.path.join(BASE_DIR, "logs")
# 内置 OCR 模型目录（随 exe 发布，避免目标机器联网下载）；不存在则走默认缓存
MODEL_DIR = os.path.join(BASE_DIR, "paddle_models")

# 状态机关键词（按优先级判定）
WORD_GROUP_END = "再来一组"   # 一组做完的结果页
WORD_NEXT = "下一题"          # 判分页（含答案解析）
WORD_FINISH = "完成"          # 本组最后一题判分页（替代“下一题”）
WORD_SUBMIT = "确定"          # 已选择选项后的提交按钮
WORD_CORRECT_ANSWER = "正确答案"  # 判分页答案解析中的正确答案
WORD_LOADING = "加载中"        # 判分页刚出现时残留的加载提示（截图需等它消失）
# 提交/判分页的按钮（确定 / 下一题 / 完成）固定在页面顶部四分之一内；
# 用位置约束避免题干或答案解析里出现同样的词时误判状态
BUTTON_TOP_RATIO = 0.25

# 默认参数（毫秒）
DEFAULT_PROMPT = "请给出这道题的答案。只给出选项，不用给出解析。不需要给出任何分析过程，只给出答案选项。如果单选，给出一个大写字母选项；如果多选，选项间用逗号隔开：\n"
DEFAULT_AI_WAIT_MS = 10000     # 等待外部 AI 作答
DEFAULT_CLICK_RESPONSE_MS = 2000  # 点击按钮后等待页面响应
DEFAULT_STEP_MS = 300          # 操作间微延迟
DEFAULT_QUESTION_LIMIT = 20    # 启动后答题数量上限

# 预览设置
PREVIEW_WIDTH = 360            # 预览画布显示宽度（像素）
PREVIEW_INTERVAL = 1.0         # 预览刷新间隔（秒）
PREVIEW_INTERVAL_RUNNING = 3.0  # 自动化运行中的预览间隔，减少与截图互斥竞争
ADB_TIMEOUT = 15               # adb 命令超时（秒）
MAX_CONSECUTIVE_FAILURES = 5   # 连续识别/截图失败多少次后自动停止
MAX_PREVIEW_FAILURES = 10      # 预览连续截图失败多少次后判定设备断开
MAX_LONG_Q_SCROLLS = 3         # 长题滚动拼接的最大滚动次数
SCROLL_SETTLE_S = 0.8          # 每次滚动后等待页面稳定的秒数

# AI 对话区自动滚动到页末（应对回答后不自动滚到页底的 AI 网页）
AI_SCROLL_CLICKS = 5           # 滚轮向下滚动次数
AI_SCROLL_UNITS = 300          # 每次滚动量（Windows 滚轮单位）
DEFAULT_SCROLL_SETTLE_MS = 1000  # 全部滚动结束后、OCR 识别前的等待（毫秒，界面可改）

# AI 答案轮询重试（固定等待期满后答案可能尚未生成，如深度检索类 AI）
AI_ANSWER_RETRY_S = 15         # 固定等待期满后，最多再轮询等待的秒数
AI_ANSWER_POLL_S = 1.5         # 两次轮询识别的间隔秒数

# OCR 引擎：见 ocr_engine.py（懒加载单例 + 后台预热）。
# 这里只保留模型目录路径，import 本模块不再触发 20~30 秒的模型加载。
