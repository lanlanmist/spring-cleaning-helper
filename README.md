# 欢迎使用 大扫除小助手 V2.2

## ChangeLog

- **V2.2**: 清理了早期开发中不再使用的废弃代码块，移除冗余功能；新增 OCR 识别区域配置的本地持久化；增加"截图日后再学"功能，支持将特定题目截图保存至本地。
- **V2.1**: 实现自选刷题组数功能，用户可根据需求自定义自动化运行的轮次。
- **V2.0**: 实现自动化循环流程，支持连续处理 5 道题目；新增自动点选答案、自动提交及自动翻页功能。
- **V1.1**: 代码重构：将原本 main.py 拆分为三个独立的功能模块。
- **V1.0**: 完成核心功能开发，支持单项题目识别。

## 环境搭建

- **scrcpy**: 下载scrcoy，从https://scrcpy.org/
- **环境依赖**: 建议新建立一个Python3.9虚拟环境搭建
```python
pip install -r requirements.txt
```
requirements_original是全量环境，requirements_slim是pipreqs生成。
请先尝试requirements_slim。
注意使用CPU版本的paddle。
- **手机端**: 打开adb调试，如有“安全设置（允许模拟点击）”选项，一并打开。


## 快速开始

- **scrcpy**: 查看连接的设备

```cmd
adb devices
```
获取到已连接设备的ID，如果同时使用安卓虚拟机，需要排查手机设备是哪个，部分安卓虚拟机支持自定义设备ID，能帮助排查。
而后使用scrcpy连接到手机
```cmd
scrcpy -s "YOUR DEVICE ID" --always-on-top --turn-screen-off --stay-awake
```
建议使用上述命令，包含连接画面置顶，关闭屏幕，不自动锁屏。可按需调整。

- **页面布局**: 请打开手机画面，千问-Qwen3-Flash模型网页，本程序窗口，均放置在前页。

- **千问-Qwen3-Flash**: 回答速度较快，推荐使用。


**祝您使用愉快！**