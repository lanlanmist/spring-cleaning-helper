"""PaddleOCR 引擎的懒加载单例与后台预热。

此前 config.py 在模块级直接构造 PaddleOCR：任何 import config 的代码
（只需要常量的 adb_service、离线验证脚本、单元测试）都要先等近 30 秒
模型加载。现在改为首次真正识别时才加载，并由主窗口在启动时开后台线程
预热，把这段等待从"窗口迟迟不出现"变成"窗口秒开、后台加载"。

注意：屏蔽 Paddle 杂讯的环境变量仍由 config.py 在 import 时注入，
本模块必须先 import config 再延迟 import paddleocr，顺序不能颠倒。
"""

import os
import threading
import time

import config

_lock = threading.Lock()
_instance = None
_error = None
_ready = threading.Event()   # 加载结束（成功或失败）都会置位


def _model_kwargs():
    """扫描随程序发布的本地模型目录；三套模型不全则回退默认缓存。"""
    if not os.path.isdir(config.MODEL_DIR):
        return {}
    subs = {}
    for key in ("det", "rec", "cls"):
        path = os.path.join(config.MODEL_DIR, key)
        if os.path.isdir(path):
            subs[key] = next((os.path.join(path, name)
                              for name in os.listdir(path)
                              if name.endswith("_infer")), None)
    if not all(subs.values()):
        return {}
    return {"det_model_dir": subs["det"],
            "rec_model_dir": subs["rec"],
            "cls_model_dir": subs["cls"]}


def get_ocr():
    """返回 PaddleOCR 单例；首次调用会一直阻塞到模型加载完毕。

    加载失败会把异常缓存下来，后续调用直接重抛，避免反复尝试拖慢流程。
    """
    global _instance, _error
    if _instance is not None:
        return _instance
    with _lock:                       # 预热线程与工作线程可能同时首调
        if _instance is not None:
            return _instance
        if _error is not None:
            raise _error
        from paddleocr import PaddleOCR        # 延迟导入：真正的耗时点
        try:
            _instance = PaddleOCR(lang="ch", use_angle_cls=False,
                                  show_log=False, **_model_kwargs())
        except Exception as e:
            _error = e
            _ready.set()
            raise
        _ready.set()
        return _instance


def is_ocr_ready():
    """模型是否已加载完成且可用。"""
    return _instance is not None


def ocr_error():
    """加载失败的异常对象（未失败为 None）。"""
    return _error


def wait_ocr_ready(timeout=None):
    """等待加载结束，返回 True 表示可用。"""
    _ready.wait(timeout)
    return _instance is not None


def warm_up_ocr():
    """预加载模型，返回 (是否成功, 耗时秒, 异常对象或 None)。"""
    started = time.perf_counter()
    try:
        get_ocr()
    except Exception as e:
        return False, time.perf_counter() - started, e
    return True, time.perf_counter() - started, None
