#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ghost Reader — 幽灵文本阅读器
================================
一个用于上班摸鱼的透明文本阅读器。

核心特性:
  - 窗口无边框、完全透明，只显示不透明的文本
  - 默认几乎不可见（1% 不透明度）且鼠标穿透
  - 鼠标移入时窗口显现，可滚动阅读
  - 鼠标移出时恢复隐藏状态
  - 支持拖拽 / 打开 .txt 文件
  - 内置文件库，自动收集导入的文本文件
  - 老板键 Ctrl+Shift+H 全局隐藏 / 显示
  - 窗口位置、字体、颜色等配置自动保存

依赖:
  pip install PyQt5 keyboard

运行:
  python ghost_reader.py
"""

import sys
import os
import shutil
import subprocess

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTextEdit, QVBoxLayout,
    QHBoxLayout, QMenu, QSystemTrayIcon, QStyle, QSizeGrip,
    QMessageBox, QFileDialog, QFontDialog, QColorDialog, QLabel,
    QListWidget, QListWidgetItem, QPushButton,
)
from PyQt5.QtCore import (
    Qt, QTimer, QSettings, QPoint, pyqtSignal,
)
from PyQt5.QtGui import QFont, QColor, QCursor

# 尝试导入 keyboard 库用于全局热键
try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False


# ==================== 全局常量 ====================
APP_NAME = "GhostReader"
ORG_NAME = "GhostReader"

BOSS_KEY = "ctrl+shift+h"           # 老板键

DEFAULT_FONT_FAMILY = "Consolas"     # 默认字体（等宽，像代码）
DEFAULT_FONT_SIZE = 12               # 默认字号
DEFAULT_TEXT_COLOR = "#CCCCCC"       # 默认文字颜色（亮灰，仿 IDE 代码）
DEFAULT_WIDTH = 800                  # 默认窗口宽度
DEFAULT_HEIGHT = 600                 # 默认窗口高度

OPACITY_HIDDEN = 0.01               # 隐藏状态不透明度（1%，肉眼几乎不可见）
OPACITY_SHOWN = 1.0                 # 显现状态不透明度（100%）

MOUSE_POLL_MS = 50                   # 鼠标位置轮询间隔（毫秒）
SAVE_DELAY_MS = 500                  # 配置延迟保存间隔（毫秒）
TITLE_BAR_HEIGHT = 28                # 自定义标题栏高度（像素）

LIBRARY_DIR = os.path.join(          # 文件库目录（用户主目录下）
    os.path.expanduser("~"), "GhostReader", "library"
)
LIBRARY_PANEL_WIDTH = 200            # 文件库侧边栏宽度


# ============================================================
#  自定义部件
# ============================================================

class GhostTextEdit(QTextEdit):
    """
    支持文件拖拽的只读文本编辑器。

    当用户将文本文件拖入时，发射 file_dropped 信号携带文件路径。
    """

    file_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    # ---- 拖拽事件 ----
    def dragEnterEvent(self, event):
        """拖拽进入: 仅接受包含文件 URL 的事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        """拖拽移动"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        """拖拽放下: 读取第一个文件路径"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path and os.path.isfile(path):
                    self.file_dropped.emit(path)
                    event.acceptProposedAction()
                    return
            super().dropEvent(event)
        else:
            super().dropEvent(event)


class TitleBar(QWidget):
    """
    极窄的透明标题栏，用于拖动窗口。

    鼠标在此区域按下并移动可拖动整个无边框窗口。
    """

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self._drag_offset = QPoint()
        self.setFixedHeight(TITLE_BAR_HEIGHT)

    def mousePressEvent(self, event):
        """鼠标按下: 记录拖动起始偏移"""
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.main_window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        """鼠标移动: 拖动窗口到新位置"""
        if event.buttons() & Qt.LeftButton:
            self.main_window.move(event.globalPos() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        """鼠标释放"""
        event.accept()


# ============================================================
#  主窗口
# ============================================================

class GhostReader(QMainWindow):
    """
    幽灵文本阅读器主窗口。

    状态机:
      HIDDEN       → 几乎不可见 + 鼠标穿透（默认）
      VISIBLE      → 完全不透明 + 可交互（鼠标在窗口内时）
      BOSS_HIDDEN  → 完全隐藏（老板键触发）
    """

    # 跨线程信号: 老板键从 keyboard 后台线程发送到 Qt 主线程
    boss_key_signal = pyqtSignal()

    def __init__(self):
        super().__init__()

        # ---- 配置 ----
        self.settings = QSettings(ORG_NAME, APP_NAME)

        # ---- 状态变量 ----
        self.is_visible_mode = False        # 是否处于显现模式
        self.is_boss_hidden = False         # 老板键隐藏状态
        self.is_always_on_top = True        # 始终置顶
        self.last_file_path = ""            # 上次打开的文件路径
        self.is_library_visible = False     # 文件库侧边栏是否可见
        self._hotkey_handle = None          # keyboard 热键句柄

        # ---- 文件库目录 ----
        self._init_library_dir()

        # ---- 构建 UI ----
        self._init_window_flags()
        self._init_ui()
        self._init_connections()

        # ---- 加载持久化配置 ----
        self._load_settings()

        # ---- 定时器 ----
        self._init_mouse_timer()
        self._init_save_timer()

        # ---- 系统托盘 ----
        self._init_tray_icon()

        # ---- 全局热键 ----
        self._init_boss_key()

        # ---- 初始状态: 隐藏 ----
        self._apply_hidden_state()

    # ============================================================
    #  初始化
    # ============================================================

    def _init_window_flags(self):
        """设置窗口标志: 无边框 + 置顶 + Tool(不在任务栏显示)"""
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        # 背景完全透明
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _init_ui(self):
        """构建界面布局: 水平布局 = 文件库侧边栏 + 阅读区"""
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)

        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(0)

        # ---------- 文件库侧边栏（可隐藏） ----------
        self._create_library_panel()
        self.library_panel.setVisible(False)
        main_layout.addWidget(self.library_panel)

        # ---------- 右侧主区域 ----------
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # 标题栏（可拖动）
        self.title_bar = TitleBar(self)
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setStyleSheet("""
            QWidget#titleBar {
                background-color: rgba(35, 35, 35, 200);
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
        """)
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(10, 0, 4, 0)
        self.title_label = QLabel("Ghost Reader  —  Ctrl+Shift+H 隐藏/显示", self.title_bar)
        self.title_label.setStyleSheet("color: #777; font-size: 11px; background: transparent;")
        title_layout.addWidget(self.title_label)
        title_layout.addStretch()
        right_layout.addWidget(self.title_bar)

        # 文本编辑器
        self.text_edit = GhostTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: rgba(22, 22, 22, 190);
                color: #CCCCCC;
                border: none;
                border-bottom-left-radius: 6px;
                border-bottom-right-radius: 6px;
                padding: 12px;
                selection-background-color: rgba(80, 80, 80, 150);
            }
            QScrollBar:vertical {
                background: rgba(40, 40, 40, 120);
                width: 8px;
                margin: 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(130, 130, 130, 160);
                min-height: 30px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(160, 160, 160, 200);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        right_layout.addWidget(self.text_edit, 1)
        main_layout.addWidget(right_widget, 1)

        # ---------- 右下角大小调整手柄 ----------
        self.size_grip = QSizeGrip(central)
        self.size_grip.setFixedSize(16, 16)
        self.size_grip.setStyleSheet("background-color: transparent;")

    # ============================================================
    #  文件库面板
    # ============================================================

    def _init_library_dir(self):
        """创建文件库目录（如果不存在）"""
        if not os.path.exists(LIBRARY_DIR):
            os.makedirs(LIBRARY_DIR)

    def _create_library_panel(self):
        """创建文件库侧边面板"""
        self.library_panel = QWidget(self)
        self.library_panel.setObjectName("libraryPanel")
        self.library_panel.setFixedWidth(LIBRARY_PANEL_WIDTH)
        self.library_panel.setStyleSheet("""
            QWidget#libraryPanel {
                background-color: rgba(30, 30, 30, 200);
                border-top-left-radius: 6px;
                border-bottom-left-radius: 6px;
                border-right: 1px solid rgba(60, 60, 60, 150);
            }
        """)

        layout = QVBoxLayout(self.library_panel)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(6)

        # 标题
        header = QLabel("文件库", self.library_panel)
        header.setStyleSheet(
            "color: #999; font-size: 12px; font-weight: bold; background: transparent;"
        )
        layout.addWidget(header)

        # 文件列表
        self.library_list = QListWidget(self.library_panel)
        self.library_list.setStyleSheet("""
            QListWidget {
                background-color: rgba(20, 20, 20, 150);
                border: none;
                color: #BBBBBB;
                font-size: 12px;
                outline: none;
            }
            QListWidget::item {
                padding: 6px 8px;
                border-radius: 4px;
            }
            QListWidget::item:hover {
                background-color: rgba(60, 60, 60, 150);
            }
            QListWidget::item:selected {
                background-color: rgba(80, 80, 120, 180);
                color: #FFFFFF;
            }
            QScrollBar:vertical {
                background: rgba(40, 40, 40, 120);
                width: 6px;
                margin: 0;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(130, 130, 130, 160);
                min-height: 30px;
                border-radius: 3px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self.library_list.setContextMenuPolicy(Qt.CustomContextMenu)
        layout.addWidget(self.library_list, 1)

        # 底部按钮
        btn_style = """
            QPushButton {
                background-color: rgba(60, 60, 60, 180);
                color: #CCCCCC;
                border: none;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: rgba(80, 80, 80, 200);
            }
            QPushButton:pressed {
                background-color: rgba(50, 50, 50, 180);
            }
        """
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)
        btn_import = QPushButton("导入", self.library_panel)
        btn_import.setStyleSheet(btn_style)
        btn_import.clicked.connect(self._import_to_library)
        btn_delete = QPushButton("删除", self.library_panel)
        btn_delete.setStyleSheet(btn_style)
        btn_delete.clicked.connect(self._delete_from_library)
        btn_layout.addWidget(btn_import)
        btn_layout.addWidget(btn_delete)
        layout.addLayout(btn_layout)

        # 打开目录按钮（整行）
        btn_explorer = QPushButton("在资源管理器中打开", self.library_panel)
        btn_explorer.setStyleSheet(btn_style)
        btn_explorer.clicked.connect(lambda: self._open_library_in_explorer())
        layout.addWidget(btn_explorer)

        # 信号连接
        self.library_list.itemDoubleClicked.connect(self._open_library_item)
        self.library_list.customContextMenuRequested.connect(
            self._show_library_context_menu
        )

    def _refresh_library(self):
        """刷新文件库列表"""
        self.library_list.clear()
        if not os.path.isdir(LIBRARY_DIR):
            return
        files = sorted(
            [f for f in os.listdir(LIBRARY_DIR)
             if f.lower().endswith(('.txt', '.md', '.log'))],
            key=str.lower
        )
        for f in files:
            item = QListWidgetItem(f)
            item.setToolTip(os.path.join(LIBRARY_DIR, f))
            self.library_list.addItem(item)

    def _toggle_library_panel(self):
        """切换文件库侧边栏显示/隐藏"""
        self.is_library_visible = not self.is_library_visible
        self.library_panel.setVisible(self.is_library_visible)
        if self.is_library_visible:
            self._refresh_library()

    def _import_to_library(self):
        """选择外部文件并导入到文件库目录"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要导入的文本文件", "",
            "文本文件 (*.txt *.md *.log);;所有文件 (*.*)"
        )
        if not paths:
            return
        imported = 0
        for path in paths:
            try:
                dest = os.path.join(LIBRARY_DIR, os.path.basename(path))
                shutil.copy2(path, dest)
                imported += 1
            except Exception as e:
                QMessageBox.warning(
                    self, "导入失败",
                    f"无法导入 {os.path.basename(path)}:\n{e}"
                )
        if imported > 0:
            self._refresh_library()

    def _open_library_item(self, item):
        """双击文件库列表项: 打开文件"""
        file_path = os.path.join(LIBRARY_DIR, item.text())
        if os.path.isfile(file_path):
            self._load_file(file_path)

    def _delete_from_library(self):
        """删除文件库中选中的文件（仅删除库中副本）"""
        item = self.library_list.currentItem()
        if not item:
            return
        file_name = item.text()
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要从文件库中删除 \"{file_name}\" 吗?\n"
            f"(仅删除文件库中的副本, 不影响原文件)",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            file_path = os.path.join(LIBRARY_DIR, file_name)
            try:
                os.remove(file_path)
                self._refresh_library()
            except Exception as e:
                QMessageBox.warning(self, "删除失败", str(e))

    def _open_library_in_explorer(self, file_path=None):
        """
        在 Windows 资源管理器中打开文件库目录。

        如果指定了 file_path，则打开资源管理器并选中该文件；
        否则直接打开文件库目录。
        """
        if not os.path.isdir(LIBRARY_DIR):
            self._init_library_dir()
        try:
            if file_path and os.path.isfile(file_path):
                # 打开资源管理器并选中指定文件
                subprocess.Popen(['explorer', '/select,', file_path])
            else:
                # 直接打开文件库目录
                os.startfile(LIBRARY_DIR)
        except Exception as e:
            QMessageBox.warning(self, "打开失败", f"无法打开资源管理器:\n{e}")

    def _show_library_context_menu(self, pos):
        """文件库列表右键菜单"""
        item = self.library_list.itemAt(pos)
        menu = QMenu(self)

        if item:
            act_open = menu.addAction("打开")
            act_open.triggered.connect(lambda: self._open_library_item(item))
            menu.addSeparator()
            file_path = os.path.join(LIBRARY_DIR, item.text())
            act_explorer_file = menu.addAction("在资源管理器中显示")
            act_explorer_file.triggered.connect(
                lambda: self._open_library_in_explorer(file_path)
            )
            menu.addSeparator()
            act_del = menu.addAction("删除")
            act_del.triggered.connect(self._delete_from_library)
        else:
            # 空白处右键: 只提供打开目录
            act_explorer_dir = menu.addAction("在资源管理器中打开")
            act_explorer_dir.triggered.connect(
                lambda: self._open_library_in_explorer()
            )

        menu.exec_(self.library_list.mapToGlobal(pos))

    def _init_connections(self):
        """连接信号与槽"""
        self.text_edit.customContextMenuRequested.connect(self._show_context_menu)
        self.text_edit.file_dropped.connect(self._load_file)
        self.boss_key_signal.connect(self._toggle_boss_key)

    def _init_mouse_timer(self):
        """
        初始化鼠标位置轮询定时器。

        由于隐藏状态下启用了鼠标穿透 (WA_TransparentForMouseEvents)，
        Qt 的 enterEvent / leaveEvent 不会触发，
        因此必须用 QTimer 轮询 QCursor.pos() 来检测鼠标是否在窗口内。
        """
        self.mouse_timer = QTimer(self)
        self.mouse_timer.setInterval(MOUSE_POLL_MS)
        self.mouse_timer.timeout.connect(self._check_mouse_position)
        self.mouse_timer.start()

    def _init_save_timer(self):
        """初始化配置延迟保存定时器（避免频繁写配置）"""
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(SAVE_DELAY_MS)
        self.save_timer.timeout.connect(self._save_settings)

    def _init_tray_icon(self):
        """初始化系统托盘图标和菜单"""
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(
            self.style().standardIcon(QStyle.SP_FileDialogContentsView)
        )
        self.tray_icon.setToolTip("Ghost Reader — 幽灵阅读器")

        tray_menu = QMenu()
        act_toggle = tray_menu.addAction("显示 / 隐藏  (Ctrl+Shift+H)")
        act_toggle.triggered.connect(self._toggle_boss_key)
        tray_menu.addSeparator()
        act_library = tray_menu.addAction("文件库")
        act_library.triggered.connect(self._toggle_library_panel)
        act_open = tray_menu.addAction("打开文件...")
        act_open.triggered.connect(self._open_file_dialog)
        tray_menu.addSeparator()
        act_quit = tray_menu.addAction("退出")
        act_quit.triggered.connect(self._quit_app)
        self.tray_icon.setContextMenu(tray_menu)

        # 单击托盘图标 = 切换显示/隐藏
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _init_boss_key(self):
        """
        注册全局热键 Ctrl+Shift+H。

        使用 keyboard 库在后台线程监听全局按键，
        触发时通过 pyqtSignal 发送到 Qt 主线程，确保线程安全。
        """
        if not KEYBOARD_AVAILABLE:
            QMessageBox.warning(
                self, "依赖缺失",
                "未安装 keyboard 库，老板键功能不可用。\n"
                "请运行: pip install keyboard"
            )
            return
        try:
            self._hotkey_handle = keyboard.add_hotkey(
                BOSS_KEY, self._on_boss_key_pressed
            )
        except Exception as e:
            QMessageBox.warning(self, "热键注册失败", str(e))

    # ============================================================
    #  鼠标检测与状态切换（核心逻辑）
    # ============================================================

    def _check_mouse_position(self):
        """
        定时器回调: 检查鼠标全局坐标是否在窗口矩形内。

        - 鼠标进入窗口 且 当前隐藏 → 执行显现
        - 鼠标离开窗口 且 当前显现 → 执行隐藏
        - 使用状态变量防止重复设置
        """
        if self.is_boss_hidden:
            return

        mouse_pos = QCursor.pos()
        rect = self.geometry()
        in_window = rect.contains(mouse_pos)

        if in_window and not self.is_visible_mode:
            self._apply_visible_state()
        elif not in_window and self.is_visible_mode:
            self._apply_hidden_state()

    def _apply_hidden_state(self):
        """
        隐藏状态:
          - 窗口不透明度 1%（肉眼几乎不可见）
          - 开启鼠标穿透（点击穿透到后面的窗口）
        """
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setWindowOpacity(OPACITY_HIDDEN)
        self.is_visible_mode = False

    def _apply_visible_state(self):
        """
        显现状态:
          - 关闭鼠标穿透（可接收鼠标事件、滚动、拖拽）
          - 窗口不透明度 100%
        """
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setWindowOpacity(OPACITY_SHOWN)
        self.is_visible_mode = True

    # ============================================================
    #  老板键
    # ============================================================

    def _on_boss_key_pressed(self):
        """
        keyboard 库后台线程回调。
        通过 pyqtSignal 转发到 Qt 主线程执行，确保线程安全。
        """
        self.boss_key_signal.emit()

    def _toggle_boss_key(self):
        """切换老板键隐藏 / 显示"""
        if self.is_boss_hidden:
            # 恢复显示
            self.is_boss_hidden = False
            self.show()
            self.mouse_timer.start()
            self._apply_hidden_state()
        else:
            # 隐藏窗口（完全不可见）
            self.is_boss_hidden = True
            self.mouse_timer.stop()
            self.hide()

    def _on_tray_activated(self, reason):
        """托盘图标激活: 单击切换显示/隐藏"""
        if reason == QSystemTrayIcon.Trigger:
            self._toggle_boss_key()

    # ============================================================
    #  文件加载与显示
    # ============================================================

    def _open_file_dialog(self):
        """打开文件选择对话框"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择文本文件", "",
            "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if path:
            self._load_file(path)

    def _load_file(self, file_path):
        """
        读取文本文件并显示。

        如果文件不在文件库目录中，自动复制一份到库中。
        自动尝试多种编码: utf-8 → gbk → gb2312 → big5 → latin-1
        """
        # 文件不在库中 → 自动导入到库
        if os.path.abspath(os.path.dirname(file_path)) != os.path.abspath(LIBRARY_DIR):
            try:
                dest = os.path.join(LIBRARY_DIR, os.path.basename(file_path))
                if not os.path.exists(dest):
                    shutil.copy2(file_path, dest)
                    if self.is_library_visible:
                        self._refresh_library()
                file_path = dest
            except Exception:
                pass  # 导入失败不影响打开原文件

        try:
            content = self._read_file_auto(file_path)
            self.text_edit.setPlainText(content)
            self.last_file_path = file_path
            self.title_label.setText(
                f"Ghost Reader  —  {os.path.basename(file_path)}"
            )
            self.settings.setValue("last_file", file_path)
            # 滚动到顶部
            self.text_edit.verticalScrollBar().setValue(0)
        except Exception as e:
            QMessageBox.warning(self, "读取失败", f"无法读取文件:\n{e}")

    @staticmethod
    def _read_file_auto(file_path):
        """自动检测编码并读取文件内容"""
        encodings = ["utf-8", "gbk", "gb2312", "big5", "latin-1"]
        for enc in encodings:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    return f.read()
            except (UnicodeDecodeError, UnicodeError):
                continue
        # 所有编码均失败, 用 latin-1 强制读取（不会抛异常）
        with open(file_path, "r", encoding="latin-1") as f:
            return f.read()

    # ============================================================
    #  右键菜单
    # ============================================================

    def _show_context_menu(self, pos):
        """在文本区域弹出右键菜单"""
        menu = QMenu(self)

        act_library = menu.addAction(
            "隐藏文件库" if self.is_library_visible else "显示文件库"
        )
        act_library.triggered.connect(self._toggle_library_panel)

        act_open = menu.addAction("打开文件...")
        act_open.triggered.connect(self._open_file_dialog)

        menu.addSeparator()

        act_top = menu.addAction(
            "取消始终置顶" if self.is_always_on_top else "始终置顶"
        )
        act_top.triggered.connect(self._toggle_always_on_top)

        menu.addSeparator()

        act_font = menu.addAction("设置字体...")
        act_font.triggered.connect(self._choose_font)

        act_color = menu.addAction("设置文字颜色...")
        act_color.triggered.connect(self._choose_color)

        menu.addSeparator()

        act_help = menu.addAction("老板键说明")
        act_help.triggered.connect(self._show_help)

        menu.addSeparator()

        act_quit = menu.addAction("退出程序")
        act_quit.triggered.connect(self._quit_app)

        menu.exec_(self.text_edit.mapToGlobal(pos))

    def _toggle_always_on_top(self):
        """切换窗口始终置顶"""
        self.is_always_on_top = not self.is_always_on_top
        flags = self.windowFlags()
        if self.is_always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        # setWindowFlags 后需要重新设置透明属性并 show
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.show()
        # 恢复之前的状态
        if self.is_visible_mode:
            self._apply_visible_state()
        else:
            self._apply_hidden_state()

    def _choose_font(self):
        """打开字体选择对话框"""
        current = self.text_edit.font()
        font, ok = QFontDialog.getFont(current, self, "选择字体")
        if ok:
            self.text_edit.setFont(font)
            self.settings.setValue("font_family", font.family())
            self.settings.setValue("font_size", font.pointSize())

    def _choose_color(self):
        """打开颜色选择对话框"""
        current = QColor(self.settings.value("text_color", DEFAULT_TEXT_COLOR))
        color = QColorDialog.getColor(current, self, "选择文字颜色")
        if color.isValid():
            self.text_edit.setTextColor(color)
            self.settings.setValue("text_color", color.name())

    def _show_help(self):
        """显示老板键说明"""
        QMessageBox.information(
            self, "老板键说明",
            "全局热键: Ctrl + Shift + H\n\n"
            "按下后立即隐藏阅读器窗口（完全不可见）。\n"
            "再次按下恢复显示。\n\n"
            "隐藏状态下鼠标移入窗口区域可自动显现文本。\n"
            "鼠标移出后自动恢复隐藏。\n\n"
            "也可单击系统托盘图标切换显示/隐藏。\n\n"
            "文件库:\n"
            "  右键菜单 → 显示文件库 可打开侧边栏。\n"
            "  拖入或打开的外部文件会自动导入到文件库。\n"
            "  文件库路径: ~/GhostReader/library/"
        )

    # ============================================================
    #  配置持久化
    # ============================================================

    def _load_settings(self):
        """从 QSettings 加载窗口位置、大小、字体、颜色等"""
        # 窗口几何位置
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
            screen = QApplication.primaryScreen()
            if screen:
                sg = screen.availableGeometry()
                self.move(
                    (sg.width() - DEFAULT_WIDTH) // 2,
                    (sg.height() - DEFAULT_HEIGHT) // 2,
                )

        # 字体
        family = self.settings.value("font_family", DEFAULT_FONT_FAMILY)
        size = int(self.settings.value("font_size", DEFAULT_FONT_SIZE))
        self.text_edit.setFont(QFont(family, size))

        # 文字颜色
        color = self.settings.value("text_color", DEFAULT_TEXT_COLOR)
        self.text_edit.setTextColor(QColor(color))

        # 上次打开的文件（自动加载）
        last_file = self.settings.value("last_file", "")
        if last_file and os.path.isfile(last_file):
            self._load_file(last_file)

    def _save_settings(self):
        """保存配置到 QSettings"""
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("font_family", self.text_edit.font().family())
        self.settings.setValue("font_size", self.text_edit.font().pointSize())
        self.settings.setValue("text_color", self.text_edit.textColor().name())
        if self.last_file_path:
            self.settings.setValue("last_file", self.last_file_path)

    # ============================================================
    #  事件处理
    # ============================================================

    def moveEvent(self, event):
        """窗口移动: 触发延迟保存"""
        self.save_timer.start()
        super().moveEvent(event)

    def resizeEvent(self, event):
        """窗口大小改变: 触发延迟保存 + 重新定位 SizeGrip"""
        self.save_timer.start()
        grip = 16
        self.size_grip.move(
            self.width() - grip - 6,
            self.height() - grip - 6,
        )
        super().resizeEvent(event)

    def keyPressEvent(self, event):
        """
        键盘事件:
          PgUp / PgDn  → 翻页
          ↑ / ↓        → 逐行滚动
          Home / End   → 跳到顶部 / 底部
        """
        if not self.is_visible_mode:
            return

        bar = self.text_edit.verticalScrollBar()
        key = event.key()

        if key == Qt.Key_PageDown:
            bar.setValue(bar.value() + bar.pageStep())
        elif key == Qt.Key_PageUp:
            bar.setValue(bar.value() - bar.pageStep())
        elif key == Qt.Key_Home:
            bar.setValue(bar.minimum())
        elif key == Qt.Key_End:
            bar.setValue(bar.maximum())
        elif key == Qt.Key_Down:
            bar.setValue(bar.value() + bar.singleStep())
        elif key == Qt.Key_Up:
            bar.setValue(bar.value() - bar.singleStep())
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        """关闭窗口: 保存配置 + 清理热键 + 隐藏托盘"""
        self._save_settings()
        self._cleanup_keyboard()
        self.tray_icon.hide()
        event.accept()

    # ============================================================
    #  退出与清理
    # ============================================================

    def _cleanup_keyboard(self):
        """移除全局热键"""
        if KEYBOARD_AVAILABLE and self._hotkey_handle is not None:
            try:
                keyboard.remove_hotkey(self._hotkey_handle)
            except Exception:
                pass
            self._hotkey_handle = None

    def _quit_app(self):
        """退出程序"""
        self._save_settings()
        self._cleanup_keyboard()
        self.tray_icon.hide()
        QApplication.quit()


# ============================================================
#  程序入口
# ============================================================

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setStyle("Fusion")

    reader = GhostReader()
    reader.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
