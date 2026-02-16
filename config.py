import os
import ctypes
from paddleocr import PaddleOCR

# DPI 适配
try:
    ctypes.windll.user32.SetProcessDPIAware()
except:
    pass

# 屏蔽 Paddle 杂讯
os.environ.update({
    "FLAGS_use_onednn": "0",
    "FLAGS_enable_pir_api": "0",
    "PADDLE_DISABLE_PIR": "1"
})

print("正在热机 PaddleOCR...")
ocr = PaddleOCR(lang="ch", use_angle_cls=False, show_log=False)
print("OCR 就绪\n")
