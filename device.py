"""设备管理与实时预览（OCRApp 的一个能力混入）。

原先与状态机、UI 构建混在同一个 1500 行的类里：设备/预览只依赖 adb 与
Tk 画布，与识别逻辑无关，单独成模块后一处改动只影响一处。
"""

import threading

import tkinter as tk
from PIL import Image, ImageTk

import ui_widgets
from adb_service import AdbError
from config import (MAX_PREVIEW_FAILURES, PREVIEW_INTERVAL,
                    PREVIEW_INTERVAL_RUNNING, SCRCPY_OPTIONS)


class DeviceMixin:
    """adb 设备枚举/连接/初始化与手机实时预览。"""

    # ==================================================================
    # 设备管理
    # ==================================================================
    def refresh_devices(self):
        threading.Thread(target=self._refresh_devices_worker, daemon=True).start()

    def _refresh_devices_worker(self):
        try:
            devices = self.adb.list_devices()
        except AdbError as e:
            self.log(f"刷新设备失败：{e}")
            return

        if not devices:
            self.log("未发现已授权的 adb 设备，请检查 USB 连接与调试开关。")
        else:
            self.log(f"发现 {len(devices)} 台设备。")
        self.root.after(0, self._fill_device_combo, devices)

    def _fill_device_combo(self, devices):
        """把设备列表填进下拉框，并直接选中一台。

        按序列号排序取第一台：等效用户在下拉里点一下，多设备时
        也不会因为 adb 返回顺序变化而连到另一台。
        """
        self._device_list = sorted(devices, key=lambda d: d[0])
        values = [f"{s} - {m}" for s, m in self._device_list]
        self.device_combo["values"] = values
        if values:
            self.device_combo.set(values[0])
            self.device_combo.current(0)
            self._set_device_status(f"发现 {len(values)} 台设备")
        else:
            self.device_combo.set("")
            self._set_device_status("未发现设备")

    def _set_device_status(self, text, color=None):
        """更新状态行；不传颜色时用当前主题的次要文字色。"""
        self.device_info_label.config(text=text,
                                      fg=ui_widgets.MUTED if color is None
                                      else color)

    def _restore_device_text(self):
        """下拉框被点开、又在别处点了一下时，Tk 可能把显示清空。

        已刷出设备但文字空了就补回来，否则用户得重新点一次列表项。
        """
        if self.device_combo.get():
            return
        devices = getattr(self, "_device_list", [])
        if not devices:
            return
        values = [f"{s} - {m}" for s, m in devices]
        self.device_combo.set(values[0])
        self.device_combo.current(0)

    def _selected_device(self):
        """当前选中的 (serial, model)；索引失效时按显示文本兑底。"""
        devices = getattr(self, "_device_list", [])
        if not devices:
            return None
        idx = self.device_combo.current()
        if 0 <= idx < len(devices):
            return devices[idx]
        text = self.device_combo.get()
        for item in devices:
            if text == f"{item[0]} - {item[1]}":
                return item
        return None

    def connect_device(self):
        picked = self._selected_device()
        if picked is None:
            self.log("请先刷新并选择设备。")
            return
        serial, model = picked
        threading.Thread(target=self._connect_worker,
                         args=(serial, model), daemon=True).start()

    def _scrcpy_var(self, key):
        """某个附加选项是否勾选（控件未建时当作未勾选）。"""
        var = getattr(self, "_scrcpy_vars", {}).get(key)
        return bool(var.get()) if var is not None else False

    def _scrcpy_options(self):
        """把勾选项翻译成 (scrcpy 参数, adb shell 步骤, 文案列表)。"""
        picked = [opt for opt in SCRCPY_OPTIONS if self._scrcpy_var(opt["key"])]
        flags = tuple(f for opt in picked for f in opt["scrcpy"])
        steps = tuple(s for opt in picked for s in opt["shell"])
        return flags, steps, [opt["label"] for opt in picked]

    def _connect_worker(self, serial, model):
        self.adb.serial = serial
        try:
            w, h = self.adb.get_resolution()
            density = self.adb.get_density()
        except AdbError as e:
            self.adb.serial = None
            self.log(f"连接设备失败：{e}")
            return

        self.device_serial = serial
        self.device_resolution = (w, h)
        self.log(f"已连接 {model} ({serial})，分辨率 {w}x{h}，密度 {density}。")

        # 按界面勾选项追加：系统项走 adb shell，画质/镜像项走 scrcpy 参数
        flags, steps, labels = self._scrcpy_options()
        summary = " / ".join(labels) if labels else "未勾选，按 scrcpy 默认参数启动"
        self.log(f"附加设置：{summary}。")
        for desc, err in self.adb.apply_shell_steps(steps):
            if err:
                self.log(f"设备设置失败（{desc}）：{err}")
            else:
                self.log(f"设备设置完成：{desc}")
        try:
            used = self.adb.launch_scrcpy(flags)
            self.log(f"scrcpy 镜像已启动（{used}）。")
        except AdbError as e:
            self.log(f"scrcpy 未启动：{e}")
        self.root.after(0, self.device_info_label.config,
                        {"text": f"已连接: {model} ({serial})  {w}x{h}  密度 {density}",
                         "fg": "#4CAF50"})
        self._start_preview()

    # ==================================================================
    # 实时预览
    # ==================================================================
    def _start_preview(self):
        if self._preview_thread and self._preview_thread.is_alive():
            return
        self._preview_stop.clear()
        self._preview_thread = threading.Thread(target=self._preview_loop,
                                                daemon=True)
        self._preview_thread.start()
        self.log("实时预览已启动。")

    def _preview_loop(self):
        failures = 0
        while not self._preview_stop.is_set():
            # 自动化运行期间降低取帧频率：adb 截图是串行资源，预览每次抢锁
            # 都会直接摊在单题耗时上（OCR 与长题滑动期间尤为明显）
            interval = (PREVIEW_INTERVAL_RUNNING if self.is_running
                        else PREVIEW_INTERVAL)
            frame = None
            if self.adb_lock.acquire(timeout=0.5):
                try:
                    if self.adb.serial:
                        frame = self.adb.screencap()
                except AdbError as e:
                    failures += 1
                    self.log(f"预览截图失败：{e}")
                    if failures >= MAX_PREVIEW_FAILURES:
                        self.log(f"预览连续 {failures} 次截图失败，"
                                 "判定设备已断开，自动停止。")
                        self._preview_stop.set()
                        self.root.after(0, self.stop_task)
                        break
                finally:
                    self.adb_lock.release()
            if frame is not None:
                failures = 0
                try:
                    self.root.after(0, self._show_preview, frame)
                except (tk.TclError, RuntimeError):
                    return
            self._preview_stop.wait(interval)
        # 线程退出（停止/断开）后把最后一帧清掉，恢复成“无画面”的占位框
        try:
            self.root.after(0, self._clear_preview)
        except (tk.TclError, RuntimeError):
            pass

    def _clear_preview(self):
        self.preview_canvas.delete("all")
        self._preview_size = None
        self._draw_preview_frame()

    def _show_preview(self, frame):
        # 左列收起时画布只剩 1px，算出来的图既看不见又要白跑一次缩放
        if not getattr(self, "_preview_visible", True):
            return
        # 按画布实际可用区域等比缩放并居中（窗口拉伸时自动适配）
        cw = self.preview_canvas.winfo_width()
        ch = self.preview_canvas.winfo_height()
        if cw <= 1 or ch <= 1:      # 窗口刚建立时尺寸未定，重新测一次
            self.preview_canvas.update_idletasks()
            cw = self.preview_canvas.winfo_width()
            ch = self.preview_canvas.winfo_height()
        cw, ch = max(1, cw), max(1, ch)
        img_h, img_w = frame.shape[:2]
        # 只在原图/画布尺寸变化时重算缩放尺寸，不每帧重复算
        size_key = (img_w, img_h, cw, ch)
        if size_key != self._preview_size:
            scale = min(cw / img_w, ch / img_h)
            self._preview_scaled = (max(1, int(img_w * scale)),
                                    max(1, int(img_h * scale)))
            self._preview_size = size_key
        # 预览只用于人眼观察，BILINEAR 足够且比 LANCZOS 快很多
        img = Image.fromarray(frame).resize(self._preview_scaled,
                                           Image.BILINEAR)
        self._tk_img = ImageTk.PhotoImage(img)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(cw // 2, ch // 2,
                                         anchor="center", image=self._tk_img,
                                         tags="photo")
