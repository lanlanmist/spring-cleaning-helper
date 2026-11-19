"""图像预处理：OCR 前抹除浅粉色水印。

答题 APP 全屏覆盖斜向文字水印（用户ID + 日期时间），颜色为浅粉色：
R 通道比 G/B 通道高约 10~16，整体亮度 > 100，占比约 0.4%。
将命中像素的 R 拉平到 G（去红偏、保留原有明暗），即可把水印
"溶进"背景，避免 OCR 把水印文字识别出来或干扰正文识别。
"""

import numpy as np

from layout_profile import DEFAULT_LAYOUT

# 水印判定阈值见 layout_profile（wm_red_excess / wm_min_r / wm_strong_excess）：
# 水印主体偏红约 10~16，抗锯齿边缘可低至 3~6，故偏红阈值取 4。


def remove_watermark(img, layout=DEFAULT_LAYOUT):
    """抹除浅粉色水印，返回同尺寸 RGB numpy 数组。

    - 强偏红像素（水印主体）：置为近白 (250,250,250)；
    - 弱偏红像素（抗锯齿边缘）：R 拉平到 G，仅去色偏。
    黑色正文 R≈G≈B、蓝色控件 B>R，均不会被误伤。
    """
    out = img.astype(np.int16)
    r, g, b = out[:, :, 0], out[:, :, 1], out[:, :, 2]
    excess_g = r - g
    excess_b = r - b
    base = (excess_g > layout.wm_red_excess) \
        & (excess_b > layout.wm_red_excess) & (r > layout.wm_min_r)
    strong = base & (excess_g > layout.wm_strong_excess)
    weak = base & ~strong
    out[strong] = 250
    out[weak, 0] = out[weak, 1]
    return out.astype(np.uint8)
