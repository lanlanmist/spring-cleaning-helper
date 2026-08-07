"""adb 封装服务：设备发现、分辨率查询、快速截图、模拟点击。

adb 可执行文件查找顺序：项目内 adb/adb.exe -> 系统 PATH。
所有命令均通过 subprocess 调用，统一超时并抛出 AdbError。
"""

import io
import os
import re
import shutil
import subprocess

import numpy as np
from PIL import Image

from config import ADB_DIR, ADB_TIMEOUT


class AdbError(Exception):
    """adb 调用失败（找不到可执行文件 / 超时 / 返回非零 / 输出异常）。"""


def find_adb():
    """按优先级查找 adb 可执行文件，找不到返回 None。"""
    local = os.path.join(ADB_DIR, "adb.exe")
    if os.path.isfile(local):
        return local
    return shutil.which("adb")


def find_scrcpy():
    """按优先级查找 scrcpy 可执行文件，找不到返回 None。"""
    local = os.path.join(ADB_DIR, "scrcpy.exe")
    if os.path.isfile(local):
        return local
    return shutil.which("scrcpy")


class AdbService:
    def __init__(self):
        self.adb_path = find_adb()
        self.scrcpy_path = find_scrcpy()
        self.serial = None
        self._scrcpy_proc = None

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------
    def ensure_adb(self):
        if not self.adb_path:
            raise AdbError("未找到 adb：请把 scrcpy 目录中的 adb.exe、"
                           "AdbWinApi.dll、AdbWinUsbApi.dll 复制到项目 adb/ 目录，"
                           "或将 adb 加入系统 PATH。")

    def _run(self, args, binary=False):
        """执行 adb 命令，返回 stdout（binary=True 时为 bytes，否则为 str）。"""
        self.ensure_adb()
        cmd = [self.adb_path] + args
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=ADB_TIMEOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            raise AdbError(f"adb 命令超时({ADB_TIMEOUT}s)：{' '.join(args[:3])}...")
        except OSError as e:
            raise AdbError(f"adb 无法执行：{e}")

        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", "ignore").strip()
            raise AdbError(f"adb 返回错误：{stderr or proc.returncode}")

        if binary:
            return proc.stdout
        return proc.stdout.decode("utf-8", "ignore")

    def _device_args(self):
        if not self.serial:
            raise AdbError("尚未选择设备，请先刷新并连接设备。")
        return ["-s", self.serial]

    # ------------------------------------------------------------------
    # 设备信息
    # ------------------------------------------------------------------
    def list_devices(self):
        """返回 [(serial, model), ...]，仅包含处于 device 状态的设备。"""
        out = self._run(["devices", "-l"])
        devices = []
        for line in out.splitlines()[1:]:
            tokens = line.split()
            if len(tokens) < 2 or tokens[1] != "device":
                continue
            serial = tokens[0]
            m = re.search(r"model:(\S+)", line)
            model = m.group(1) if m else "未知机型"
            devices.append((serial, model))
        return devices

    def get_resolution(self):
        """返回设备物理分辨率 (width, height)。"""
        out = self._run(self._device_args() + ["shell", "wm", "size"])
        m = re.search(r"(\d+)x(\d+)", out)
        if not m:
            raise AdbError(f"无法解析分辨率：{out.strip()}")
        return int(m.group(1)), int(m.group(2))

    def get_density(self):
        """返回像素密度，解析失败返回 None。"""
        out = self._run(self._device_args() + ["shell", "wm", "density"])
        m = re.search(r"(\d+)", out)
        return int(m.group(1)) if m else None

    # ------------------------------------------------------------------
    # 截图与操作
    # ------------------------------------------------------------------
    def screencap(self):
        """全屏截图，返回 RGB 顺序的 numpy 数组。"""
        data = self._run(self._device_args() + ["exec-out", "screencap", "-p"],
                         binary=True)
        if not data:
            raise AdbError("截图返回为空，请检查设备连接。")
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
        except (OSError, ValueError):
            raise AdbError("截图数据解码失败。")
        return np.array(img)

    def tap(self, x, y):
        """在设备坐标 (x, y) 处模拟点击。"""
        self._run(self._device_args() + ["shell", "input", "tap",
                                         str(int(x)), str(int(y))])

    def swipe(self, x1, y1, x2, y2, duration_ms=400):
        """从 (x1, y1) 滑动到 (x2, y2)。向上滑（y2<y1）使页面下滚。"""
        self._run(self._device_args() +
                  ["shell", "input", "swipe", str(int(x1)), str(int(y1)),
                   str(int(x2)), str(int(y2)), str(int(duration_ms))])

    # ------------------------------------------------------------------
    # 连接后设备准备（防锁屏 / 调低亮度）
    # ------------------------------------------------------------------
    def prepare_device(self, brightness=30):
        """连接后一次性设置，返回 [(描述, 错误信息或 None), ...]。

        单项失败不阻断，由调用方记录日志。
        """
        steps = [
            ("保持屏幕常亮(USB 连接期间不自动锁屏)",
             ["shell", "svc", "power", "stayon", "usb"]),
            ("亮度改为手动模式",
             ["shell", "settings", "put", "system", "screen_brightness_mode", "0"]),
            (f"亮度调低至 {brightness}",
             ["shell", "settings", "put", "system", "screen_brightness",
              str(int(brightness))]),
        ]
        results = []
        for desc, args in steps:
            try:
                self._run(self._device_args() + args)
                results.append((desc, None))
            except AdbError as e:
                results.append((desc, str(e)))
        return results

    # ------------------------------------------------------------------
    # scrcpy 镜像（连接后自动启动）
    # ------------------------------------------------------------------
    def launch_scrcpy(self, extra_args=("--turn-screen-off", "--stay-awake",
                                        "--video-bit-rate", "10M",
                                        "--max-fps", "60")):
        """后台启动 scrcpy 镜像，设备屏幕关闭但保持唤醒。

        已在运行则不重复启动；找不到 scrcpy 抛 AdbError。
        """
        if self._scrcpy_proc and self._scrcpy_proc.poll() is None:
            return "scrcpy 已在运行"
        if not self.scrcpy_path:
            raise AdbError("未找到 scrcpy.exe：请把 scrcpy 目录整体复制到项目 "
                           "adb/ 目录（至少包含 scrcpy.exe 及其依赖文件），"
                           "或将 scrcpy 加入系统 PATH。")
        cmd = [self.scrcpy_path, "-s", self.serial] + list(extra_args)
        try:
            self._scrcpy_proc = subprocess.Popen(
                cmd, cwd=os.path.dirname(self.scrcpy_path),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            raise AdbError(f"scrcpy 启动失败：{e}")
        return " ".join(extra_args)
