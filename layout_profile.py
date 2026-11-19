"""布局参数集中定义。

原先这些"魔法比例"散落在 quiz_parser.py、image_clean.py、config.py 与
ocr_app.py 中：同一个 0.09 的行距兜底出现三次、0.3 既表示"宽行"又表示
滑动手势位置，改一处需要全文搜索且极易漏改。

这里把它们收敛为不可变的 LayoutProfile，所有比例均相对截图宽/高：
  - 解析侧（quiz_parser）通过 layout= 参数注入，便于按机型微调；
  - 视觉清洗（image_clean）的水印阈值同样归此；
  - 设备操作（滑动手势、顶部按钮区）与解析共用同一份常量。

默认值与原实现逐字一致，重构不改变任何识别/点击行为。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LayoutProfile:
    """答题页布局与视觉阈值（比例均为相对屏幕宽/高的小数）。"""

    # --- 左侧选项字母竖带 ---
    option_band_ratio: float = 0.20      # 竖带宽度 / 屏宽
    band_narrow_ratio: float = 0.20      # 竖带内"窄框"上限（多为水印残留）
    band_wide_ratio: float = 0.30        # 选项正文行的最小宽度

    # --- 纵向聚类与行距估计 ---
    row_tol_ratio: float = 0.02          # 视觉行聚类容差
    merge_tol_ratio: float = 0.012       # 题干重排 / 选项区边界的回退容差
    question_extend_gap_ratio: float = 0.06  # 无前缀页扩展题干时的最大行间距
    question_zone_start_ratio: float = 0.10  # 题干起点的最小 y（避开水印与状态栏）
    option_zone_end_ratio: float = 0.75      # 无"查看提示"时选项区的最大 y
    default_row_gap_ratio: float = 0.09      # 行距估计失败时的兜底
    row_gap_match_ratio: float = 0.60        # 内容行归属选项行的最大偏差（×行距）
    slot_search_ratio: float = 0.50          # 缺失前缀回填的候选搜索半径（×行距）
    gap_median_keep_ratio: float = 0.60      # 行距众数估计时剔除过小间隔

    # --- 手机页面操作 ---
    button_top_ratio: float = 0.25       # 顶部按钮区（确定/下一题/完成）高度占比
    scroll_from_ratio: float = 0.80      # 滑动手势起点 y 占比
    scroll_to_ratio: float = 0.30        # 滑动手势终点 y 占比

    # --- 水印抹除阈值（RGB 0~255） ---
    wm_red_excess: int = 4               # R 相对 G/B 的最小偏红量
    wm_min_r: int = 100                  # 判定水印的最低 R 亮度
    wm_strong_excess: int = 8            # 强偏红（水印笔画主体）阈值


# 全局默认布局。若某机型排版明显不同，可在此另建 profile 实例注入解析函数。
DEFAULT_LAYOUT = LayoutProfile()
