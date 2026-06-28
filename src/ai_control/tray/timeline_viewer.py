from __future__ import annotations

import argparse
import ctypes
import json
import locale
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_control.segments.mkv_container import decode_mkv_frame_to_image
from ai_control.runtime_platform import is_windows
from ai_control.tray.state import default_agent_dir
from ai_control.tray.viewers import build_timeline_model


def launch_timeline_viewer_process(*, mode: str = "timeline") -> dict[str, Any]:
    """Start the Qt timeline viewer without blocking the tray process."""
    env = os.environ.copy()
    source_root = _source_repo_root_for_launcher()
    exe = _packaged_timeline_viewer_exe()
    if source_root is not None:
        command = ["py", "-B", "-m", "ai_control.tray.timeline_viewer", "--mode", mode]
        cwd = source_root
        env["PYTHONPATH"] = str(source_root / "src") + os.pathsep + env.get("PYTHONPATH", "")
        launch_kind = "source_qt_viewer"
    elif exe.exists():
        command = [str(exe), "--mode", mode]
        cwd = exe.parent
        launch_kind = "packaged_qt_viewer"
    else:
        command = [sys.executable, "-m", "ai_control.tray.timeline_viewer", "--mode", mode]
        cwd = Path.cwd()
        launch_kind = "current_python_qt_viewer"
    stopped = _stop_existing_timeline_viewers()
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **_hidden_subprocess_kwargs(),
    )
    report = {
        "object_type": "AgentSightTimelineViewerLaunchReport",
        "schema": "agentsight_timeline_viewer_qt_v1",
        "viewer": "pyside6_qt_native_window",
        "launch_kind": launch_kind,
        "mode": mode,
        "pid": process.pid,
        "command": command,
        "cwd": str(cwd),
        "stopped_existing_viewers": stopped,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": _boundary(),
    }
    _write_timeline_launch_report(report)
    return report


def _packaged_timeline_viewer_exe() -> Path:
    return Path(sys.executable).resolve().parent / "AgentSightTimelineViewer.exe"


def _source_repo_root_for_launcher() -> Path | None:
    candidates = [
        Path(sys.executable).resolve().parent.parent,
        Path.cwd(),
        Path(__file__).resolve().parents[3],
    ]
    for root in candidates:
        try:
            if (root / "src" / "ai_control" / "tray" / "timeline_viewer.py").exists():
                return root
        except Exception:
            continue
    return None


def _hidden_subprocess_kwargs() -> dict[str, Any]:
    startupinfo = None
    if is_windows():
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def _stop_existing_timeline_viewers() -> dict[str, Any]:
    exe = _packaged_timeline_viewer_exe()
    if not is_windows():
        return {"attempted": False, "reason": "not_packaged_windows_viewer"}
    commands = []
    results = []
    if exe.exists():
        commands.append(["taskkill", "/F", "/IM", exe.name])
    commands.append([
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'AgentSight Timeline*' } | Stop-Process -Force",
    ])
    for command in commands:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            **_hidden_subprocess_kwargs(),
        )
        results.append({
            "command": command,
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-500:],
            "stderr_tail": completed.stderr[-500:],
        })
    return {
        "attempted": True,
        "results": results,
    }


def _write_timeline_launch_report(report: dict[str, Any]) -> None:
    try:
        path = default_agent_dir() / "last-timeline-viewer-launch.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _viewer_language() -> str:
    settings = default_agent_dir() / "tray-settings.json"
    try:
        payload = json.loads(settings.read_text(encoding="utf-8"))
        language = str(payload.get("language") or "system")
    except Exception:
        language = "system"
    if language in {"zh", "en"}:
        return language
    if is_windows():
        try:
            langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
            if langid & 0x03FF == 0x0004:
                return "zh"
        except Exception:
            pass
    try:
        code = (locale.getlocale()[0] or locale.getdefaultlocale()[0] or "").lower()
    except Exception:
        code = ""
    return "zh" if code.startswith("zh") else "en"


def _viewer_text(language: str) -> dict[str, str]:
    if language == "zh":
        return {
            "frames": "帧",
            "logs": "历史记录",
            "history": "历史记录",
            "refresh": "刷新",
            "load_prev": "往前加载",
            "load_next": "往后加载",
            "play": "播放",
            "pause": "暂停",
            "speed": "速度",
            "selected": "选中帧",
            "retry": "重新解码",
            "decoding": "正在解码当前帧...",
            "decode_failed": "当前帧无法解码",
            "decode_pending": "当前段仍在写入，稍后可重试",
            "frame_time": "录制时间",
        }
    return {
        "frames": "Frames",
        "logs": "History",
        "history": "History",
        "refresh": "Refresh",
        "load_prev": "Load previous",
        "load_next": "Load next",
        "play": "Play",
        "pause": "Pause",
        "speed": "Speed",
        "selected": "Selected frame",
        "retry": "Retry decode",
        "decoding": "Decoding selected frame...",
        "decode_failed": "Selected frame cannot be decoded",
        "decode_pending": "Segment is still being written; retry shortly",
        "frame_time": "Captured",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentSight native Qt timeline viewer.")
    parser.add_argument("--mode", choices=["timeline", "log"], default="timeline")
    parser.add_argument("--max-frames", type=int, default=500)
    args = parser.parse_args(argv)
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:
        print(f"PySide6 unavailable: {exc}", file=sys.stderr)
        return 4
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = TimelineWindow(max_frames=args.max_frames, initial_mode=args.mode)
    window.show()
    return int(app.exec())


def decode_frame_to_qimage(frame: dict[str, Any]) -> tuple[Any | None, dict[str, Any]]:
    """Decode a Segment frame directly into memory for Qt rendering."""
    from PIL.ImageQt import ImageQt
    from PySide6.QtGui import QImage

    restore_ref = frame.get("segment_restore_ref")
    if not isinstance(restore_ref, dict):
        return None, _decode_report("missing_restore_ref", frame=frame)
    segment_path = restore_ref.get("segment_path")
    frame_id = restore_ref.get("frame_id")
    if not segment_path or not frame_id:
        return None, _decode_report("missing_segment_path_or_frame_id", frame=frame)
    try:
        path = Path(str(segment_path))
        image, report = decode_mkv_frame_to_image(restore_ref)
        hash_ok = bool(report.get("hash_ok", True))
        qimage = QImage(ImageQt(image.convert("RGBA")))
        return qimage.copy(), {
            "status": "decoded" if hash_ok else "decoded_hash_mismatch",
            "frame_id": frame_id,
            "segment_path": str(path),
            "restore_report": _safe_report(report),
            "hash_ok": hash_ok,
            "raw_or_derived": "derived_review_memory_only",
            "file_written": False,
            "boundary": _boundary(),
        }
    except Exception as exc:
        return None, _decode_report(f"{type(exc).__name__}:{exc}", frame=frame)


class TimelineWindow:  # constructed lazily after PySide6 import is available
    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, QUrl, Signal
        from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        from PySide6.QtWidgets import (
            QComboBox,
            QHBoxLayout,
            QLabel,
            QMainWindow,
            QPushButton,
            QSlider,
            QSplitter,
            QTreeWidget,
            QTreeWidgetItem,
            QVBoxLayout,
            QScrollBar,
            QWidget,
        )

        class _FrameStrip(QWidget):
            frameSelected = Signal(int)

            def __init__(self, outer: Any) -> None:
                super().__init__()
                self.outer = outer
                self.px_per_second = 8.0
                self.scroll_x = 0.0
                self.dragging = False
                self.setMinimumHeight(86)
                self.setMouseTracking(True)

            def _second_key(self, frame: dict[str, Any]) -> int:
                value = int(frame.get("timestamp_ms") or self.outer.timeline_start_ms)
                return max(0, int(value // 1000) - self.outer.timeline_start_second)

            def _frame_x(self, frame: dict[str, Any]) -> float:
                start = self.outer.timeline_start_ms
                second = max(0, self._second_key(frame))
                return second * self.px_per_second

            def _content_width(self) -> int:
                if not self.outer.frames:
                    return self.width()
                last_second = max(int((int(frame.get("timestamp_ms") or 0)) // 1000) for frame in self.outer.frames)
                seconds = max(1, last_second - self.outer.timeline_start_second + 1)
                return max(self.width(), int(seconds * self.px_per_second + 24))

            def _clamp_scroll(self) -> None:
                self.scroll_x = max(0.0, min(self.scroll_x, max(0, self._content_width() - self.width())))
                self.outer._sync_strip_scrollbar()

            def scroll_to_end(self) -> None:
                self.scroll_x = max(0, self._content_width() - self.width())
                self.update()

            def center_on_selected(self) -> None:
                if not self.outer.frames:
                    return
                frame = self.outer.frames[self.outer.selected_index]
                self.scroll_x = self._frame_x(frame) - self.width() / 2
                self._clamp_scroll()
                self.update()

            def ensure_selected_visible(self) -> None:
                if not self.outer.frames:
                    return
                frame = self.outer.frames[self.outer.selected_index]
                x = self._frame_x(frame)
                margin = 32
                if x < self.scroll_x + margin:
                    self.scroll_x = x - margin
                    self._clamp_scroll()
                elif x > self.scroll_x + self.width() - margin:
                    self.scroll_x = x - self.width() + margin
                    self._clamp_scroll()
                self.update()

            def paintEvent(self, event: Any) -> None:
                painter = QPainter(self)
                painter.fillRect(self.rect(), QColor("#202020"))
                painter.setPen(QPen(QColor("#444"), 1))
                painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 6, 6)
                axis_y = self.height() - 24
                painter.setPen(QPen(QColor("#5c6470"), 1))
                painter.drawLine(10, axis_y, self.width() - 10, axis_y)
                bar_w = max(2, int(self.px_per_second) - 1)
                second_bins: dict[int, dict[str, Any]] = {}
                selected_center = None
                for index, frame in enumerate(self.outer.frames):
                    second = self._second_key(frame)
                    bucket = second_bins.setdefault(second, {"count": 0, "has_log": False, "indexes": []})
                    bucket["count"] += 1
                    bucket["has_log"] = bool(bucket["has_log"] or frame.get("operation_log_indexes"))
                    bucket["indexes"].append(index)
                max_count = max([int(bucket["count"]) for bucket in second_bins.values()] or [1])
                for second, bucket in second_bins.items():
                    x = int(second * self.px_per_second - self.scroll_x)
                    if x < -bar_w or x > self.width() + bar_w:
                        continue
                    has_log = bool(bucket["has_log"])
                    color = QColor("#2f7df6") if not has_log else QColor("#18995a")
                    ratio = min(1.0, (int(bucket["count"]) / max_count) ** 0.55)
                    height = max(10, int(18 + ratio * 34))
                    rect = QRect(x, axis_y - height, bar_w, height)
                    painter.fillRect(rect, color)
                    if self.outer.selected_index in bucket["indexes"]:
                        selected_center = x + bar_w // 2
                        painter.setPen(QPen(QColor("#f05a45"), 2))
                        painter.drawLine(selected_center, 8, selected_center, self.height() - 8)
                        painter.setPen(QPen(QColor("#0b0b0b"), 2))
                        painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 3, 3)
                if selected_center is None and self.outer.frames:
                    frame = self.outer.frames[self.outer.selected_index]
                    selected_center = int(self._frame_x(frame) - self.scroll_x + bar_w / 2)
                    painter.setPen(QPen(QColor("#f05a45"), 2))
                    painter.drawLine(selected_center, 8, selected_center, self.height() - 8)
                painter.end()

            def mousePressEvent(self, event: Any) -> None:
                self.dragging = True
                self._select_at(event.position().x())

            def mouseMoveEvent(self, event: Any) -> None:
                if self.dragging:
                    self._select_at(event.position().x())

            def mouseReleaseEvent(self, event: Any) -> None:
                self.dragging = False

            def _select_at(self, x_pos: float) -> None:
                if not self.outer.frames:
                    return
                content_x = x_pos + self.scroll_x
                nearest = min(
                    range(len(self.outer.frames)),
                    key=lambda idx: abs(self._frame_x(self.outer.frames[idx]) - content_x),
                )
                self.frameSelected.emit(nearest)

            def wheelEvent(self, event: Any) -> None:
                mouse_x = event.position().x()
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    content_at_mouse = self.scroll_x + mouse_x
                    seconds_at_mouse = content_at_mouse / max(0.1, self.px_per_second)
                    factor = 1.18 if event.angleDelta().y() > 0 else 0.85
                    self.px_per_second = max(1.0, min(90.0, self.px_per_second * factor))
                    self.scroll_x = seconds_at_mouse * self.px_per_second - mouse_x
                else:
                    self.scroll_x -= event.angleDelta().y() * 0.8
                self._clamp_scroll()
                self.update()

        def _make_as_icon() -> Any:
            pixmap = QPixmap(64, 64)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor("#22c7b8"), 1))
            font = QFont("Segoe UI", 24, QFont.Weight.Black)
            painter.setFont(font)
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "AS")
            painter.end()
            return QIcon(pixmap)

        class _TimelineWindow(QMainWindow):
            def __init__(self, *, max_frames: int = 500, initial_mode: str = "timeline") -> None:
                super().__init__()
                self.setWindowTitle("AgentSight Timeline")
                self.setWindowIcon(_make_as_icon())
                self.resize(1280, 820)
                self.model = build_timeline_model(max_frames=max_frames, max_logs=500)
                self.frames = list(self.model.get("frames") or [])
                self.logs = list(self.model.get("operation_logs") or [])
                self.attachments = list(self.model.get("operation_log_attachments") or [])
                self.selected_index = max(0, len(self.frames) - 1)
                self.max_frames = max_frames
                self.timeline_start_ms = min([int(frame.get("timestamp_ms") or 0) for frame in self.frames] or [0])
                self.timeline_start_second = int(self.timeline_start_ms // 1000)
                self.language = _viewer_language()
                self.text = _viewer_text(self.language)
                self.video_widget = QVideoWidget()
                self.video_widget.setMinimumHeight(420)
                self.video_widget.setStyleSheet("background:#050b14;border-radius:6px;")
                self.player = QMediaPlayer(self)
                self.audio_output = QAudioOutput(self)
                self.audio_output.setVolume(0)
                self.player.setAudioOutput(self.audio_output)
                self.player.setVideoOutput(self.video_widget)
                self.player.positionChanged.connect(self._on_player_position_changed)
                self.player.durationChanged.connect(self._on_player_duration_changed)
                self.player.mediaStatusChanged.connect(self._on_player_media_status_changed)
                self.player.errorOccurred.connect(self._on_player_error)
                self.current_segment_path = ""
                self.pending_seek_ms: int | None = None
                self.pending_prime_frame = False
                self.timeline_play_timer = QTimer(self)
                self.timeline_play_timer.setInterval(40)
                self.timeline_play_timer.timeout.connect(self._advance_timeline_playback)
                self.timeline_play_started_at_ms = 0
                self.timeline_play_start_global_ms = 0
                self._seek_generation = 0
                self._syncing_from_player = False
                self._syncing_to_player = False
                self.strip = _FrameStrip(self)
                self.status = QLabel()
                self.slider = QSlider(Qt.Orientation.Horizontal)
                self.speed = QComboBox()
                self.play_button = QPushButton()
                self.frame_time_label = QLabel("")
                self.frame_time_label.setStyleSheet("color:#cbd5e1;")
                self.history = QTreeWidget()
                self._log_items: dict[int, Any] = {}
                self._build_ui(QSplitter, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox, QSlider, QTreeWidget, QTreeWidgetItem, QScrollBar)
                self._populate_history(QTreeWidgetItem)
                if self.frames:
                    self.strip.scroll_to_end()
                    self.select_frame(self.selected_index)

            def _build_ui(self, QSplitter: Any, QWidget: Any, QVBoxLayout: Any, QHBoxLayout: Any, QPushButton: Any, QLabel: Any, QComboBox: Any, QSlider: Any, QTreeWidget: Any, QTreeWidgetItem: Any, QScrollBar: Any) -> None:
                root = QWidget()
                root.setStyleSheet("""
                    QWidget { background:#202020; color:#f2f2f2; font-family:'Microsoft YaHei UI','Segoe UI',Arial; font-size:13px; }
                    QPushButton, QComboBox { background:#2b2b2b; color:#f2f2f2; border:1px solid #555; border-radius:4px; padding:5px 12px; }
                    QPushButton:hover { background:#343434; }
                    QPushButton#primary { background:#107c72; border-color:#107c72; color:white; }
                    QTreeWidget { background:#252525; border:1px solid #444; border-radius:6px; alternate-background-color:#2c2c2c; }
                    QTreeWidget::item { min-height:28px; padding:4px; }
                    QTreeWidget::item:hover { background:#333333; color:#ffffff; }
                    QTreeWidget::item:selected { background:#107c72; color:#ffffff; }
                    QSlider::groove:horizontal { height:5px; background:#4b5563; border-radius:2px; }
                    QSlider::handle:horizontal { width:14px; margin:-5px 0; background:#16a394; border-radius:7px; }
                """)
                main = QVBoxLayout(root)
                main.setContentsMargins(10, 10, 10, 8)
                stats = QLabel(f"{self.text['frames']}: {len(self.frames)}    {self.text['logs']}: {len(self.logs)}")
                stats.setStyleSheet("color:#9ca3af;")
                main.addWidget(stats)
                splitter = QSplitter()
                left = QWidget()
                left_layout = QVBoxLayout(left)
                controls = QHBoxLayout()
                load_prev_button = QPushButton(self.text["load_prev"])
                prev_button = QPushButton("◀")
                next_button = QPushButton("▶")
                load_next_button = QPushButton(self.text["load_next"])
                refresh_button = QPushButton(self.text["refresh"])
                self.play_button.setObjectName("primary")
                self.play_button.setText("▶")
                self.play_button.setToolTip(self.text["play"])
                self.play_button.setFixedWidth(52)
                self.speed.addItems(["1", "2", "5", "10", "30"])
                self.speed.setCurrentText("5")
                self.speed.currentTextChanged.connect(lambda text: self._update_timeline_playback_rate())
                load_prev_button.clicked.connect(self.load_previous_frames)
                load_next_button.clicked.connect(self.load_next_frames)
                prev_button.clicked.connect(lambda: self._user_select_frame(max(0, self.selected_index - 1)))
                next_button.clicked.connect(lambda: self._user_select_frame(min(len(self.frames) - 1, self.selected_index + 1)))
                refresh_button.clicked.connect(self.reload_model)
                self.play_button.clicked.connect(self._toggle_playback)
                controls.addWidget(load_prev_button)
                controls.addWidget(prev_button)
                controls.addWidget(self.play_button)
                controls.addWidget(next_button)
                controls.addWidget(load_next_button)
                controls.addWidget(QLabel(self.text["speed"]))
                controls.addWidget(self.speed)
                controls.addWidget(refresh_button)
                controls.addWidget(self.frame_time_label)
                controls.addStretch(1)
                left_layout.addWidget(self.video_widget, stretch=1)
                left_layout.addLayout(controls)
                self.slider.sliderPressed.connect(self._pause_for_user_navigation)
                self.slider.sliderMoved.connect(self._seek_global_timeline)
                left_layout.addWidget(self.slider)
                self.strip.frameSelected.connect(self._user_select_frame)
                left_layout.addWidget(self.strip)
                self.strip_scroll = QScrollBar(Qt.Orientation.Horizontal)
                self.strip_scroll.valueChanged.connect(self._scroll_strip_to)
                left_layout.addWidget(self.strip_scroll)
                splitter.addWidget(left)
                right = QWidget()
                right_layout = QVBoxLayout(right)
                title = QLabel(self.text["history"])
                title.setStyleSheet("font-weight:700;font-size:18px;")
                right_layout.addWidget(title)
                self.history.setHeaderHidden(True)
                self.history.setWordWrap(True)
                self.history.setUniformRowHeights(False)
                self.history.setTextElideMode(Qt.TextElideMode.ElideNone)
                self.history.header().setStretchLastSection(True)
                self.history.setAlternatingRowColors(True)
                right_layout.addWidget(self.history)
                splitter.addWidget(right)
                splitter.setSizes([880, 380])
                main.addWidget(splitter, stretch=1)
                main.addWidget(self.status)
                self.setCentralWidget(root)

            def _populate_history(self, QTreeWidgetItem: Any) -> None:
                self.history.clear()
                self._log_items.clear()
                for index, payload in enumerate(self.logs):
                    entry = payload.get("entry") if isinstance(payload.get("entry"), dict) else {}
                    title = self._history_title(index, entry)
                    item = QTreeWidgetItem([title])
                    item.setData(0, Qt.ItemDataRole.UserRole, index)
                    for line in self._history_detail_lines(entry):
                        item.addChild(self._history_child_item(QTreeWidgetItem, line))
                    self.history.addTopLevelItem(item)
                    self._log_items[index] = item
                self.history.itemClicked.connect(self._history_clicked)

            def _history_child_item(self, QTreeWidgetItem: Any, text: str) -> Any:
                item = QTreeWidgetItem([text])
                item.setToolTip(0, text)
                visual_lines = max(1, text.count("\n") + (len(text) // 38))
                item.setSizeHint(0, QSize(260, min(120, 24 + visual_lines * 18)))
                return item

            def _history_title(self, index: int, entry: dict[str, Any]) -> str:
                route = str(entry.get("route") or entry.get("op") or "-")
                status = str(entry.get("status") or entry.get("code") or "-")
                events = int(entry.get("host_sent_event_count") or 0)
                if self.language == "zh":
                    action = "查看" if "look" in route else "操作" if "do" in route else "状态/记录"
                    return f"#{index} {action} · {route} · {status} · 输入事件 {events}"
                action = "Look" if "look" in route else "Action" if "do" in route else "Status/record"
                return f"#{index} {action} · {route} · {status} · events {events}"

            def _history_detail_lines(self, entry: dict[str, Any]) -> list[str]:
                lines: list[str] = []
                route = str(entry.get("route") or entry.get("op") or "-")
                basis = entry.get("basis") if isinstance(entry.get("basis"), dict) else {}
                readiness = entry.get("readiness") if isinstance(entry.get("readiness"), dict) else {}
                view_id = entry.get("view_id") or basis.get("view_id")
                frame_refs = entry.get("frame_refs") if isinstance(entry.get("frame_refs"), dict) else {}
                blockers = entry.get("control_blockers") or readiness.get("control_blockers") or []
                if self.language == "zh":
                    lines.append(f"接口：{route}")
                    lines.append(f"结果：{entry.get('status') or entry.get('code') or '-'}")
                    if view_id:
                        lines.append(f"使用视图：{view_id}")
                    if isinstance(frame_refs, dict):
                        total = sum(len(value) for value in frame_refs.values() if isinstance(value, list))
                        if total:
                            lines.append(f"关联帧：{total} 个")
                    if int(entry.get("host_sent_event_count") or 0):
                        lines.append(f"真实输入事件：{entry.get('host_sent_event_count')}")
                    if blockers:
                        lines.append("阻断原因：\n" + "\n".join(f"  - {item}" for item in map(str, blockers)))
                else:
                    lines.append(f"Route: {route}")
                    lines.append(f"Result: {entry.get('status') or entry.get('code') or '-'}")
                    if view_id:
                        lines.append(f"View: {view_id}")
                    if isinstance(frame_refs, dict):
                        total = sum(len(value) for value in frame_refs.values() if isinstance(value, list))
                        if total:
                            lines.append(f"Linked frames: {total}")
                    if int(entry.get("host_sent_event_count") or 0):
                        lines.append(f"Host input events: {entry.get('host_sent_event_count')}")
                    if blockers:
                        lines.append("Blockers:\n" + "\n".join(f"  - {item}" for item in map(str, blockers)))
                return lines

            def _history_clicked(self, item: Any, column: int) -> None:
                log_index = item.data(0, Qt.ItemDataRole.UserRole)
                if log_index is None and item.parent() is not None:
                    log_index = item.parent().data(0, Qt.ItemDataRole.UserRole)
                if log_index is None:
                    return
                attachment = next((item for item in self.attachments if item.get("log_index") == int(log_index)), None)
                if isinstance(attachment, dict) and attachment.get("frame_index") is not None:
                    self._user_select_frame(int(attachment["frame_index"]))

            def reload_model(self, *, keep_selected_time: bool = False) -> None:
                selected_time = None
                selected_identity = None
                if keep_selected_time and self.frames:
                    selected_frame = self.frames[self.selected_index]
                    selected_time = int(selected_frame.get("timestamp_ms") or 0)
                    selected_identity = self._frame_identity(selected_frame)
                previous_frames = list(self.frames)
                previous_index = self.selected_index
                model = build_timeline_model(max_frames=self.max_frames, max_logs=500)
                new_frames = list(model.get("frames") or [])
                if not new_frames and previous_frames:
                    self.model = model
                    self.frames = previous_frames
                    self.status.setText(self.text["decode_pending"])
                    self.strip.update()
                    self._sync_strip_scrollbar()
                    self.select_frame(min(previous_index, len(self.frames) - 1))
                    return
                self.model = model
                self.frames = new_frames
                self.logs = list(self.model.get("operation_logs") or [])
                self.attachments = list(self.model.get("operation_log_attachments") or [])
                self.timeline_start_ms = min([int(frame.get("timestamp_ms") or 0) for frame in self.frames] or [0])
                self.timeline_start_second = int(self.timeline_start_ms // 1000)
                self._populate_history(QTreeWidgetItem)
                if selected_time is not None and self.frames:
                    exact = self._find_frame_by_identity(selected_identity)
                    nearest = exact if exact is not None else min(range(len(self.frames)), key=lambda idx: abs(int(self.frames[idx].get("timestamp_ms") or 0) - selected_time))
                    self.select_frame(nearest)
                    self.strip.center_on_selected()
                else:
                    self.select_frame(max(0, len(self.frames) - 1))
                    self.strip.scroll_to_end()

            def load_previous_frames(self) -> None:
                self._pause_for_user_navigation()
                self.max_frames += 500
                self.reload_model(keep_selected_time=True)

            def load_next_frames(self) -> None:
                self._pause_for_user_navigation()
                self.reload_model(keep_selected_time=True)
                self.strip.center_on_selected()

            def select_frame(self, index: int, *, seek_player: bool = True, prime: bool = True) -> None:
                if not self.frames:
                    self.status.setText("No frames found")
                    return
                self.selected_index = max(0, min(index, len(self.frames) - 1))
                frame = self.frames[self.selected_index]
                self.frame_time_label.setText(self._format_frame_time(frame))
                if seek_player:
                    self._seek_player_to_frame(frame, prime=prime)
                self._sync_slider_for_frame(frame)
                self._sync_history_for_frame(frame)
                self.strip.ensure_selected_visible()
                self.strip.update()
                self.status.setText(f"{self.text['selected']} {self.selected_index + 1}/{len(self.frames)} · {frame.get('segment_frame_id') or frame.get('name')}")

            def _user_select_frame(self, index: int) -> None:
                self._pause_for_user_navigation()
                self.select_frame(index)

            def _seek_player_to_frame(self, frame: dict[str, Any], *, prime: bool = True) -> None:
                segment_path = self._frame_segment_path(frame)
                pts_ms = self._frame_playback_pts_ms(frame)
                if not segment_path:
                    self.status.setText("No MKV segment path for selected frame")
                    return
                self._seek_generation += 1
                generation = self._seek_generation
                self._syncing_to_player = True
                try:
                    if segment_path != self.current_segment_path:
                        self.current_segment_path = segment_path
                        self.pending_seek_ms = max(0, pts_ms)
                        self.pending_prime_frame = bool(prime)
                        self.player.setSource(QUrl.fromLocalFile(segment_path))
                        QTimer.singleShot(180, lambda value=max(0, pts_ms), do_prime=bool(prime), token=generation: self._set_player_position(value, prime=do_prime, generation=token))
                    else:
                        self._set_player_position(max(0, pts_ms), prime=prime, generation=generation)
                finally:
                    self._syncing_to_player = False

            def _set_player_position(self, value: int, *, prime: bool = True, generation: int | None = None) -> None:
                if generation is not None and generation != self._seek_generation:
                    return
                self._syncing_to_player = True
                try:
                    self.player.setPosition(max(0, int(value)))
                finally:
                    self._syncing_to_player = False
                if prime:
                    self.pending_prime_frame = True
                    QTimer.singleShot(120, lambda token=generation: self._prime_paused_video_frame(token))

            def _prime_paused_video_frame(self, generation: int | None = None) -> None:
                if generation is not None and generation != self._seek_generation:
                    return
                if not self.pending_prime_frame:
                    return
                if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                    self.pending_prime_frame = False
                    return
                self.player.play()
                QTimer.singleShot(90, lambda token=generation: self._pause_after_prime(token))

            def _pause_after_prime(self, generation: int | None = None) -> None:
                if generation is not None and generation != self._seek_generation:
                    return
                if self.pending_prime_frame:
                    self.player.pause()
                    self.pending_prime_frame = False
                    self.play_button.setText("▶")
                    self.play_button.setToolTip(self.text["play"])

            def _frame_segment_path(self, frame: dict[str, Any]) -> str:
                restore_ref = frame.get("segment_restore_ref") if isinstance(frame.get("segment_restore_ref"), dict) else {}
                return str(restore_ref.get("segment_path") or frame.get("path") or "")

            def _frame_playback_pts_ms(self, frame: dict[str, Any]) -> int:
                restore_ref = frame.get("segment_restore_ref") if isinstance(frame.get("segment_restore_ref"), dict) else {}
                try:
                    for key in ("playback_pts_ms", "player_pts_ms", "pts_ms"):
                        value = restore_ref.get(key) if restore_ref.get(key) is not None else frame.get(key)
                        if value is not None:
                            return int(value)
                    return int(frame.get("index") or 0) * 40
                except Exception:
                    return int(frame.get("index") or 0) * 40

            def _frame_global_ms(self, frame: dict[str, Any]) -> int:
                try:
                    return max(0, int(frame.get("timestamp_ms") or self.timeline_start_ms) - int(self.timeline_start_ms))
                except Exception:
                    return 0

            def _global_duration_ms(self) -> int:
                if not self.frames:
                    return 1
                end = max(int(frame.get("timestamp_ms") or self.timeline_start_ms) for frame in self.frames)
                return max(1, int(end) - int(self.timeline_start_ms))

            def _sync_slider_for_frame(self, frame: dict[str, Any]) -> None:
                value_ms = self._frame_global_ms(frame)
                self.slider.blockSignals(True)
                self.slider.setRange(0, self._global_duration_ms())
                self.slider.setValue(max(0, min(int(value_ms), self.slider.maximum())))
                self.slider.blockSignals(False)

            def _seek_global_timeline(self, value: int) -> None:
                if not self.frames:
                    return
                self._pause_for_user_navigation()
                nearest = min(range(len(self.frames)), key=lambda idx: abs(self._frame_global_ms(self.frames[idx]) - int(value)))
                self.select_frame(nearest)

            def _frame_identity(self, frame: dict[str, Any]) -> str:
                return str(
                    frame.get("segment_frame_id")
                    or frame.get("name")
                    or f"{self._frame_segment_path(frame)}::{frame.get('frame_id') or frame.get('timestamp_ms')}"
                )

            def _find_frame_by_identity(self, identity: str | None) -> int | None:
                if not identity:
                    return None
                for index, frame in enumerate(self.frames):
                    if self._frame_identity(frame) == identity:
                        return index
                return None

            def _on_player_position_changed(self, position_ms: int) -> None:
                if not self.timeline_play_timer.isActive():
                    return
                if self._syncing_to_player:
                    return
                self._syncing_from_player = True
                try:
                    if self._pause_if_past_loaded_tail(int(position_ms)):
                        return
                    self._select_frame_for_player_position(int(position_ms))
                finally:
                    self._syncing_from_player = False

            def _on_player_duration_changed(self, duration_ms: int) -> None:
                if self.frames:
                    self._sync_slider_for_frame(self.frames[self.selected_index])

            def _on_player_media_status_changed(self, status: Any) -> None:
                if self.pending_seek_ms is None:
                    return
                if status in {
                    QMediaPlayer.MediaStatus.LoadedMedia,
                    QMediaPlayer.MediaStatus.BufferedMedia,
                }:
                    value = self.pending_seek_ms
                    self.pending_seek_ms = None
                    self._set_player_position(value, prime=self.pending_prime_frame, generation=self._seek_generation)
                elif status == QMediaPlayer.MediaStatus.EndOfMedia:
                    self._play_next_segment_if_available()

            def _on_player_error(self, error: Any, error_string: str) -> None:
                self.status.setText(f"{self.text['decode_failed']}: {error_string or error}")

            def _select_frame_for_player_position(self, position_ms: int) -> None:
                if not self.frames or not self.current_segment_path:
                    return
                candidates = [
                    index
                    for index, frame in enumerate(self.frames)
                    if self._frame_segment_path(frame) == self.current_segment_path
                ]
                if not candidates:
                    return
                nearest = min(candidates, key=lambda idx: abs(self._frame_playback_pts_ms(self.frames[idx]) - position_ms))
                if nearest != self.selected_index:
                    self.select_frame(nearest, seek_player=False)
                elif nearest == len(self.frames) - 1 and position_ms > self._frame_playback_pts_ms(self.frames[nearest]) + 25:
                    self._pause_if_past_loaded_tail(position_ms)

            def _pause_if_past_loaded_tail(self, position_ms: int) -> bool:
                if not self.frames or not self.current_segment_path:
                    return False
                candidates = [
                    index
                    for index, frame in enumerate(self.frames)
                    if self._frame_segment_path(frame) == self.current_segment_path
                ]
                if not candidates:
                    return False
                last_index = max(candidates)
                if last_index != len(self.frames) - 1:
                    return False
                last_pts = self._frame_playback_pts_ms(self.frames[last_index])
                if position_ms <= last_pts + 25:
                    return False
                self.player.pause()
                self.player.setPosition(max(0, last_pts))
                self.select_frame(last_index, seek_player=False)
                self._stop_timeline_playback()
                return True

            def _play_next_segment_if_available(self) -> None:
                if not self.frames or not self.current_segment_path:
                    return
                candidates = [
                    index
                    for index, frame in enumerate(self.frames)
                    if self._frame_segment_path(frame) == self.current_segment_path
                ]
                if not candidates:
                    return
                next_index = max(candidates) + 1
                if next_index >= len(self.frames):
                    self._stop_timeline_playback()
                    return
                self.select_frame(next_index)
                self._start_timeline_playback()

            def _format_frame_time(self, frame: dict[str, Any]) -> str:
                value = frame.get("timestamp_ms")
                try:
                    if value is None:
                        return ""
                    stamp = datetime.fromtimestamp(int(value) / 1000.0).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    return f"{self.text['frame_time']}: {stamp}"
                except Exception:
                    return ""

            def _sync_strip_scrollbar(self) -> None:
                if not hasattr(self, "strip_scroll"):
                    return
                maximum = max(0, self.strip._content_width() - self.strip.width())
                self.strip_scroll.blockSignals(True)
                self.strip_scroll.setRange(0, maximum)
                self.strip_scroll.setPageStep(max(1, self.strip.width()))
                self.strip_scroll.setValue(int(self.strip.scroll_x))
                self.strip_scroll.blockSignals(False)

            def _scroll_strip_to(self, value: int) -> None:
                self.strip.scroll_x = float(value)
                self.strip._clamp_scroll()
                self.strip.update()

            def _sync_history_for_frame(self, frame: dict[str, Any]) -> None:
                indexes = [idx for idx in frame.get("operation_log_indexes") or [] if isinstance(idx, int)]
                self.history.clearSelection()
                if not indexes:
                    return
                item = self._log_items.get(indexes[0])
                if item is None:
                    return
                item.setSelected(True)
                self.history.scrollToItem(item)

            def _toggle_playback(self) -> None:
                if self.timeline_play_timer.isActive():
                    self._stop_timeline_playback()
                    return
                self._start_timeline_playback()

            def _start_timeline_playback(self) -> None:
                if not self.frames:
                    return
                self.play_button.setText("Ⅱ")
                self.play_button.setToolTip(self.text["pause"])
                self.player.pause()
                self.timeline_play_start_global_ms = self._frame_global_ms(self.frames[self.selected_index])
                self.timeline_play_started_at_ms = self._monotonic_ms()
                self.timeline_play_timer.start()

            def _stop_timeline_playback(self) -> None:
                self.timeline_play_timer.stop()
                self.player.pause()
                self.play_button.setText("▶")
                self.play_button.setToolTip(self.text["play"])

            def _pause_for_user_navigation(self) -> None:
                if self.timeline_play_timer.isActive() or self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                    self._stop_timeline_playback()

            def _update_timeline_playback_rate(self) -> None:
                if not self.timeline_play_timer.isActive() or not self.frames:
                    return
                current_global = self._frame_global_ms(self.frames[self.selected_index])
                self.timeline_play_start_global_ms = current_global
                self.timeline_play_started_at_ms = self._monotonic_ms()

            def _advance_timeline_playback(self) -> None:
                if not self.frames:
                    self._stop_timeline_playback()
                    return
                elapsed = max(0, self._monotonic_ms() - self.timeline_play_started_at_ms)
                try:
                    speed = float(self.speed.currentText())
                except Exception:
                    speed = 1.0
                target_global = self.timeline_play_start_global_ms + int(elapsed * speed)
                duration = self._global_duration_ms()
                if target_global >= duration:
                    self.select_frame(len(self.frames) - 1, prime=False)
                    self._stop_timeline_playback()
                    return
                nearest = min(range(len(self.frames)), key=lambda idx: abs(self._frame_global_ms(self.frames[idx]) - target_global))
                if nearest != self.selected_index:
                    self.select_frame(nearest, prime=False)

            def _monotonic_ms(self) -> int:
                return int(__import__("time").monotonic() * 1000)

            def resizeEvent(self, event: Any) -> None:
                super().resizeEvent(event)

        return _TimelineWindow(*args, **kwargs)


def _decode_report(reason: str, *, frame: dict[str, Any], restore_report: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "not_decoded",
        "reason": reason,
        "frame_id": frame.get("segment_frame_id") or frame.get("name"),
        "restore_report": _safe_report(restore_report or {}),
        "file_written": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": _boundary(),
    }


def _safe_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key not in {"image", "data", "bytes"}}


def _boundary() -> dict[str, bool]:
    return {
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
