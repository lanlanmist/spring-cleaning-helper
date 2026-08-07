import os
import sys
import ctypes

# --- DPI 适配（必须在创建 Tk 窗口前完成） ---
try:
    ctypes.windll.user32.SetProcessDPIAware()
except (AttributeError, OSError):
    pass

# --- 屏蔽 Paddle 杂讯（必须在 import paddleocr 之前设置才生效） ---
os.environ.update({
    "FLAGS_use_onednn": "0",
    "FLAGS_enable_pir_api": "0",
    "PADDLE_DISABLE_PIR": "1",
})

from paddleocr import PaddleOCR  # noqa: E402

# --- 基础目录：PyInstaller 冻结后锚定 exe 所在目录，源码运行时为项目目录 ---
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- 全局常量 ---
APP_VERSION = "大扫除小助手 V3.0"
PARAMS_FILE = os.path.join(BASE_DIR, "params.json")
ADB_DIR = os.path.join(BASE_DIR, "adb")
SHOT_DIR = os.path.join(BASE_DIR, "screenshots")
# 内置 OCR 模型目录（随 exe 发布，避免目标机器联网下载）；不存在则走默认缓存
MODEL_DIR = os.path.join(BASE_DIR, "paddle_models")

# 状态机关键词（按优先级判定）
WORD_GROUP_END = "再来一组"   # 一组做完的结果页
WORD_NEXT = "下一题"          # 判分页（含答案解析）
WORD_FINISH = "完成"          # 本组最后一题判分页（替代“下一题”）
WORD_SUBMIT = "确定"          # 已选择选项后的提交按钮
WORD_CORRECT_ANSWER = "正确答案"  # 判分页答案解析中的正确答案
WORD_LOADING = "加载中"        # 判分页刚出现时残留的加载提示（截图需等它消失）

# 默认参数（毫秒）
DEFAULT_PROMPT = "请给出这道题的答案。只给出选项，不用给出解析。如果单选，给出一个大写字母选项；如果多选，选项间用逗号隔开：\n"
DEFAULT_AI_WAIT_MS = 10000     # 等待外部 AI 作答
DEFAULT_CLICK_RESPONSE_MS = 2000  # 点击按钮后等待页面响应
DEFAULT_STEP_MS = 300          # 操作间微延迟
DEFAULT_QUESTION_LIMIT = 20    # 启动后答题数量上限

# 预览设置
PREVIEW_WIDTH = 360            # 预览画布显示宽度（像素）
PREVIEW_INTERVAL = 1.0         # 预览刷新间隔（秒）
ADB_TIMEOUT = 15               # adb 命令超时（秒）
MAX_CONSECUTIVE_FAILURES = 5   # 连续识别/截图失败多少次后自动停止
MAX_PREVIEW_FAILURES = 10      # 预览连续截图失败多少次后判定设备断开
MAX_LONG_Q_SCROLLS = 3         # 长题滚动拼接的最大滚动次数
SCROLL_SETTLE_S = 0.8          # 每次滚动后等待页面稳定的秒数

print("正在热机 PaddleOCR...")
_model_kwargs = {}
if os.path.isdir(MODEL_DIR):
    # 使用随程序发布的本地模型（det/rec/cls 下各有一个 *_infer 目录），
    # 离线也可运行；目录不全则整体回退默认缓存路径
    _sub = {"det": None, "rec": None, "cls": None}
    for key in _sub:
        _d = os.path.join(MODEL_DIR, key)
        if os.path.isdir(_d):
            _infer = next((os.path.join(_d, n) for n in os.listdir(_d)
                           if n.endswith("_infer")), None)
            _sub[key] = _infer
    if all(_sub.values()):
        _model_kwargs = {
            "det_model_dir": _sub["det"],
            "rec_model_dir": _sub["rec"],
            "cls_model_dir": _sub["cls"],
        }
ocr = PaddleOCR(lang="ch", use_angle_cls=False, show_log=False,
                **_model_kwargs)
print("OCR 就绪\n")
