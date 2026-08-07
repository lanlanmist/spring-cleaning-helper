# adb 驱动目录

本目录已内置完整驱动，克隆仓库后可直接使用，无需额外下载：

- **adb**：adb.exe + AdbWinApi.dll + AdbWinUsbApi.dll
- **scrcpy-win64-v3.3.4**：scrcpy.exe + scrcpy-server + 相关依赖 dll
  （SDL2.dll、libusb-1.0.dll、avcodec/avformat/avutil/swresample 等）

程序会优先使用本目录的 adb.exe / scrcpy.exe；若本目录缺失，则回退到系统 PATH。

## 自行更换版本

如需升级或更换，直接用新版文件覆盖本目录中的对应文件即可：

- 更换 scrcpy：从 [scrcpy 发布页](https://github.com/Genymobile/scrcpy/releases)
  下载 win64 压缩包，把其中的全部文件覆盖到本目录（注意保留 adb 的 3 个文件）；
- 更换 adb：替换 adb.exe、AdbWinApi.dll、AdbWinUsbApi.dll 即可。

## 镜像参数说明

连接设备后程序会自动启动 scrcpy，参数为
`--turn-screen-off --stay-awake --video-bit-rate 10M --max-fps 60`
（手机物理屏关闭但保持唤醒，镜像清晰流畅）。
