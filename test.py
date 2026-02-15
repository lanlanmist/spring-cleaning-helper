import cv2
import numpy as np
from paddleocr import PaddleOCR
import pyperclip
import os

# --- 配置区 ---
IMAGE_PATH = "Snipaste_2026-02-10_14-48-23.png"  # 你的图片路径
DEBUG_PATH = "debug_denoised.png"  # 去噪后的预览图


def denoise_and_ocr(path):
    print(f"正在读取并预处理图片: {path}")

    # 1. 图像预处理 (对抗“俄罗斯方块”黑点)
    img = cv2.imread(path)
    if img is None:
        print("❌ 错误: 找不到图片文件！")
        return

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 使用中值滤波抹除孤立黑点 (ksize=3)
    denoised = cv2.medianBlur(gray, 3)

    # 自适应二值化，让文字笔画更清晰，背景更纯净
    binary = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 11, 2
    )

    cv2.imwrite(DEBUG_PATH, binary)
    print(f"✅ 已保存去噪后的预览图至: {DEBUG_PATH}")

    # 2. 初始化 (避开参数坑，3.x 自动识别 GPU)
    try:
        ocr = PaddleOCR(lang="ch")
        # 强制关闭预处理器，防止它因为图片干扰而乱转画面
        if hasattr(ocr, 'pipeline'):
            ocr.pipeline.use_doc_preprocessor = False
    except Exception as e:
        print(f"❌ 引擎初始化失败: {e}")
        return

    # 3. 识别 (使用 3.x 的 predict 接口)
    print("正在进行推理...")
    # 将单通道二值图转回 3 通道以适配模型输入
    input_img = cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB)
    result = ocr.predict(input_img)

    # 4. 深度提取文字 (针对 3.x 嵌套字典结构)
    def extract_texts(obj):
        texts = []
        if isinstance(obj, dict):
            if 'rec_texts' in obj: return obj['rec_texts']
            for v in obj.values():
                res = extract_texts(v)
                if res: texts.extend(res)
        elif isinstance(obj, list):
            for i in obj:
                res = extract_texts(i)
                if res: texts.extend(res)
        return texts

    final_texts = extract_texts(result)
    clean_text = "".join([str(t) for t in final_texts]).strip()

    if clean_text:
        pyperclip.copy(clean_text)
        print(f"\n🎉 识别成功!")
        print(f"内容预览: {clean_text[:50]}...")
    else:
        print("\n⚠️ 识别为空。请检查 debug_denoised.png：")
        print("如果字被抹白了，请尝试把第 23 行 medianBlur(gray, 3) 改为 1。")


if __name__ == "__main__":
    denoise_and_ocr(IMAGE_PATH)
