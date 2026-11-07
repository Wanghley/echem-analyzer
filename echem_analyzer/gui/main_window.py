"""Main window: toolbar, file dock, tabs, welcome page, project workflow and exports."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileDialog, QFrame, QLabel, QMainWindow, QMenu, QMessageBox, QSizePolicy,
    QStackedWidget, QTabWidget, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import __version__, about, theme
from ..models import KIND_CA, KIND_CV
from ..prefs import prefs
from ..project import SUFFIX, ProjectError, collect_results, load_project
from ..report import analysed_runs, build_report
from ..report_pdf import write_pdf
from ..workbook import export_workbook
from .analysis_tab import AnalysisTab
from .ca_tab import CATab
from .ui_kit import Splitter, persist_splitters
from .compare_tab import CompareTab
from .cv_tab import CVTab
from .electrode_tab import ElectrodeTab
from .help_dialog import HelpDialog
from .import_dialog import ImportDialog
from .measurements_tab import MeasurementsTab
from .project_dialog import ProjectInfoDialog
from .settings_dialog import SettingsDialog
from .session import Session, curve_short_label
from .study_tab import StudyTab
from .support import SupportCard, coffee_button, open_link
from .welcome import WelcomePage

FILTER = "PalmSens exports (*.csv *.pssession);;All files (*)"
PROJECT_FILTER = f"Echem Analyzer project (*{SUFFIX});;All files (*)"

SAVE, DISCARD, CANCEL = "save", "discard", "cancel"
PAGE_WELCOME, PAGE_WORK = 0, 1


@contextmanager
def wait_cursor():
    QApplication.setOverrideCursor(Qt.WaitCursor)
    QApplication.processEvents()
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


class MainWindow(QMainWindow):
    def __init__(self, session: Optional[Session] = None):
        super().__init__()
        self.setAcceptDrops(True)
        self.session = session or Session()
        app = QApplication.instance()
        theme.apply(app, str(self.session.qs.value("theme", "system")))
        app.setWindowIcon(QIcon(theme.app_logo(128)))
        app.styleHints().colorSchemeChanged.connect(self._on_system_scheme)

        self.tabs = QTabWidget()
        self.measurements_tab = MeasurementsTab(self.session)
        self.ca_tab = CATab(self.session)
        self.cv_tab = CVTab(self.session)
        self.analysis_tab = AnalysisTab(self.session)
        self.electrode_tab = ElectrodeTab(self.session)
        self.study_tab = StudyTab(self.session)
        self.compare_tab = CompareTab(self.session)
        for w, name in ((self.measurements_tab, "Measurements"), (self.ca_tab, "Standard addition"),
                        (self.cv_tab, "Voltammetry"), (self.analysis_tab, "Analysis"), (self.electrode_tab, "Electrode"),
                        (self.study_tab, "Sensor study"), (self.compare_tab, "Compare")):
            self.tabs.addTab(w, name)
        self.measurements_tab.open_in_analysis.connect(self.open_in_analysis)
        self.measurements_tab.import_requested.connect(self.import_table_dialog)
        self.analysis_tab.message.connect(lambda m: self.statusBar().showMessage(m, 8000))

        self.welcome = WelcomePage()
        self.welcome.open_project.connect(self.open_project_dialog)
        self.welcome.add_files.connect(self.open_files)
        self.welcome.add_folder.connect(self.open_folder)
        self.welcome.open_recent.connect(self.open_project_from)
        self.welcome.open_help.connect(self.show_help)
        self.welcome.open_settings.connect(self.show_settings)
        self.welcome.return_to_project.connect(self.show_work)
        self.welcome.close_project.connect(self.close_project)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)

        self._build_dock()
        self._build_menus()
        self._build_toolbar()
        self.support_card = SupportCard(self)                  # floats in the corner; hidden until asked for
        self.support_card.never_show.connect(self._support_never)
        self.count_label = QLabel("")
        self.statusBar().addPermanentWidget(self.count_label)
        self.session.files_changed.connect(self._on_files_changed)
        self.session.project_changed.connect(self._update_title)
        self.session.project_changed.connect(self._populate_recent_menu)
        theme.bus.changed.connect(self._on_theme_changed)
        self._on_files_changed()
        self._update_title()
        self.statusBar().showMessage("Open a project, or add data files (or drop them onto the window).")

        for tab, prefix in ((self.measurements_tab, "measurements"), (self.ca_tab, "ca"), (self.cv_tab, "cv"), (self.analysis_tab, "analysis"),
                            (self.electrode_tab, "electrode"), (self.study_tab, "study"), (self.compare_tab, "compare")):
            persist_splitters(tab, self.session.qs, prefix)           # every grip remembers where you left it
        geo = self.session.qs.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1560, 960)
        self._restore_dock_width()

    # ================================================================== chrome
    def _build_dock(self) -> None:
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.setFrameShape(QFrame.NoFrame)
        self.file_tree.itemDoubleClicked.connect(self._on_tree_double_click)
        self.file_tree.setToolTip("Double-click a CA run to analyse it, or a CV scan to open its run.")
        dock = QDockWidget("FILES", self)
        dock.setObjectName("files_dock")
        dock.setWidget(self.file_tree)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        dock.setMinimumWidth(150)
        self.resizeDocks([dock], [self.DOCK_DEFAULT], Qt.Horizontal)
        self.files_dock = dock

    DOCK_DEFAULT = 270

    def _restore_dock_width(self) -> None:
        try:
            w = int(float(self.session.qs.value("layout/filesDockWidth", 0)))
        except (TypeError, ValueError):
            w = 0
        if 150 <= w <= 900:
            self.resizeDocks([self.files_dock], [w], Qt.Horizontal)

    def reset_layout(self) -> None:
        """Back to the default panel sizes everywhere (the files panel, every side panel and every plot / table split)."""
        for sp in self.findChildren(Splitter):
            sp.reset_sizes()
        self.resizeDocks([self.files_dock], [self.DOCK_DEFAULT], Qt.Horizontal)
        self.session.qs.remove("layout/filesDockWidth")
        self.statusBar().showMessage("Panel sizes reset.", 4000)

    def _action(self, menu, text, slot, shortcut=None) -> QAction:
        a = QAction(text, self)
        if shortcut is not None:
            a.setShortcut(shortcut)
        a.triggered.connect(lambda _checked=False: slot())
        menu.addAction(a)
        return a

    def _build_menus(self) -> None:
        m = self.menuBar().addMenu("&File")
        self._action(m, "New project", self.new_project, QKeySequence.New)
        self.act_open = self._action(m, "Open project…", self.open_project_dialog, QKeySequence.Open)
        self.recent_menu = m.addMenu("Open recent")
        self.recent_menu.aboutToShow.connect(self._populate_recent_menu)
        self._populate_recent_menu()                                       # filled already: a native menu bar may not ask first
        self.act_save = self._action(m, "Save project", self.save_project, QKeySequence.Save)
        self._action(m, "Save project as…", self.save_project_as, QKeySequence.SaveAs)
        self._action(m, "Project info…", self.edit_project_info)
        self.act_close = self._action(m, "Close project", self.close_project, QKeySequence("Ctrl+W"))
        m.addSeparator()
        self.act_add = self._action(m, "Add data files…", self.open_files, QKeySequence("Ctrl+Shift+O"))
        self._action(m, "Add data folder…", self.open_folder, QKeySequence("Ctrl+Alt+O"))
        self._action(m, "Import table (any instrument)…", self.import_table_dialog, QKeySequence("Ctrl+Shift+I"))
        self._action(m, "Remove all data", self.clear_data)
        m.addSeparator()
        self.act_pdf = self._action(m, "Export report (PDF)…", self.export_report_dialog, QKeySequence("Ctrl+E"))
        self.act_xlsx = self._action(m, "Export workbook (Excel)…", self.export_workbook_dialog,
                                     QKeySequence("Ctrl+Shift+E"))
        self.act_csv = self._action(m, "Export all results (CSV)…", self.export_all_results_dialog)
        m.addSeparator()
        self.act_settings = self._action(m, "Settings…", self.show_settings, QKeySequence("Ctrl+,"))
        m.addSeparator()
        self._action(m, "Quit", self.close, QKeySequence("Ctrl+Q"))

        v = self.menuBar().addMenu("&View")
        self.act_home = self._action(v, "Home", self.show_home, QKeySequence("Ctrl+Shift+H"))
        v.addSeparator()
        v.addAction(self.files_dock.toggleViewAction())
        self._action(v, "Reset panel sizes", self.reset_layout)
        appearance = v.addMenu("Appearance")
        grp = QActionGroup(self)
        self.theme_actions: dict[str, QAction] = {}
        for mode, label in (("system", "Match system"), ("light", "Light"), ("dark", "Dark")):
            a = QAction(label, self, checkable=True)
            a.setChecked(theme.preference() == mode)
            a.triggered.connect(lambda _c=False, md=mode: self.set_theme(md))
            grp.addAction(a)
            appearance.addAction(a)
            self.theme_actions[mode] = a

        h = self.menuBar().addMenu("&Help")
        self._action(h, "Guide", self.show_help, QKeySequence("F1"))
        self._action(h, "Keyboard shortcuts", lambda: self.show_help("shortcuts"))
        h.addSeparator()
        self._action(h, "About Echem Analyzer", self.show_about)
        self._action(h, "Show presets folder", self.show_presets_folder)
        h.addSeparator()
        self._action(h, "Buy me a coffee ☕", lambda: open_link(about.COFFEE.url))
        self._action(h, f"{about.AUTHOR}’s website", lambda: open_link(about.WEBSITE.url))

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(Qt.TopToolBarArea, tb)
        self.toolbar = tb

        self.name_label = QLabel("")
        self.name_label.setObjectName("ProjectName")
        self.sub_label = QLabel("")
        self.sub_label.setObjectName("ProjectSub")
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(4, 0, 12, 0)
        lay.setSpacing(0)
        lay.addWidget(self.name_label)
        lay.addWidget(self.sub_label)
        tb.addWidget(box)
        self._tool_sep(tb)

        self.tb_home = tb.addAction("Home", self.show_home)
        self.tb_home.setToolTip("Back to the start page (the project stays open)")
        self.tb_open = tb.addAction("Open", self.open_project_dialog)
        self.tb_save = tb.addAction("Save", self.save_project)
        self._tool_sep(tb)
        self.tb_add = tb.addAction("Add data", self.open_files)
        self._tool_sep(tb)

        self.export_btn = QToolButton()
        self.export_btn.setText("Export")
        self.export_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.export_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.export_btn)
        menu.addAction(self.act_pdf)
        menu.addAction(self.act_xlsx)
        menu.addAction(self.act_csv)
        self.export_btn.setMenu(menu)
        tb.addWidget(self.export_btn)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        # always visible; it opens the support card (which holds the link) and closes it again on a second click
        self.coffee_btn = coffee_button(compact=True, open_on_click=False)
        self.coffee_btn.clicked.connect(self.toggle_support_card)
        self.coffee_btn.setToolTip("Support Echem Analyzer\nA coffee keeps it going: " + about.COFFEE.url)
        tb.addWidget(self.coffee_btn)
        gap = QWidget()
        gap.setFixedWidth(10)                                    # a little air before the icon buttons
        tb.addWidget(gap)
        self.tb_help = tb.addAction("", lambda: self.show_help())
        self.tb_help.setToolTip("Guide (F1)")
        self.tb_settings = tb.addAction("", self.show_settings)
        self.tb_settings.setToolTip("Settings (Ctrl+,)")
        self.tb_theme = tb.addAction("", self.toggle_theme)
        self.tb_theme.setToolTip("Switch between light and dark")
        self._restyle_toolbar()

    def _tool_sep(self, tb: QToolBar) -> None:
        sep = QFrame()
        sep.setObjectName("ToolSep")
        sep.setFrameShape(QFrame.VLine)
        sep.setFixedWidth(1)
        tb.addWidget(sep)

    def _restyle_toolbar(self) -> None:
        p = theme.current()
        self.tb_home.setIcon(theme.icon("home", p.text))
        self.tb_open.setIcon(theme.icon("folder-open", p.text))
        self.tb_save.setIcon(theme.icon("save", p.text))
        self.tb_add.setIcon(theme.icon("plus", p.text))
        self.export_btn.setIcon(theme.icon("download", p.text))
        self.tb_theme.setIcon(theme.icon("sun" if p.name == "dark" else "moon", p.muted))
        self.tb_help.setIcon(theme.icon("help-circle", p.muted))
        self.tb_settings.setIcon(theme.icon("settings", p.muted))
        self._restyle_tabs()

    TAB_ICONS = ("layers", "activity", "repeat", "sliders", "zap", "bar-chart", "columns")

    def _restyle_tabs(self) -> None:
        p = theme.current()
        self.tabs.setIconSize(QSize(16, 16))
        for i, name in enumerate(self.TAB_ICONS[: self.tabs.count()]):
            self.tabs.setTabIcon(i, theme.icon(name, p.muted, 16))

    def show_settings(self) -> None:
        if getattr(self, "_settings", None) is None:
            self._settings = SettingsDialog(self.set_theme, self)
        self._settings._load()
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    def show_help(self, topic: str = "start") -> None:
        topic = topic if isinstance(topic, str) else "start"
        if getattr(self, "_help", None) is None:
            self._help = HelpDialog(self, topic)
        else:
            self._help.show_topic(topic)
        self._help.show()
        self._help.raise_()
        self._help.activateWindow()

    # ================================================================== theme
    def set_theme(self, mode: str) -> None:
        self.session.qs.setValue("theme", mode)
        theme.apply(QApplication.instance(), mode)
        if mode in self.theme_actions:
            self.theme_actions[mode].setChecked(True)

    def toggle_theme(self) -> None:
        self.set_theme("light" if theme.current().name == "dark" else "dark")

    def _on_system_scheme(self, *_args) -> None:
        if theme.preference() == "system":
            theme.apply(QApplication.instance(), "system")

    def _on_theme_changed(self) -> None:
        self._restyle_toolbar()

    # ================================================================== title / status
    def _update_title(self) -> None:
        s = self.session
        self.setWindowTitle(f"{s.display_name()}[*] — Echem Analyzer")
        self.setWindowModified(s.dirty)
        self.name_label.setText(s.display_name() + ("  ●" if s.dirty else ""))
        self.sub_label.setText(str(s.project_path) if s.project_path else "Unsaved project")
        self.sub_label.setToolTip(str(s.project_path) if s.project_path else "")
        self._update_close_enabled()

    def _update_close_enabled(self) -> None:
        s = self.session
        self.act_close.setEnabled(bool(s.files or s.project_path))

    def show_home(self) -> None:
        """The start page, with the open project kept (and one click away)."""
        if not self.session.files:
            return
        self.welcome.set_current(self.session.display_name() + ("  ●" if self.session.dirty else ""))
        self.stack.setCurrentIndex(PAGE_WELCOME)
        self.statusBar().showMessage("Home: return to your project, open another one, or close this one.")

    def show_work(self) -> None:
        if self.session.files:
            self.stack.setCurrentIndex(PAGE_WORK)
            self.statusBar().clearMessage()

    def close_project(self) -> bool:
        """Close the project (after asking about unsaved changes) and go to the start page."""
        s = self.session
        if not (s.files or s.project_path):
            return False
        if not self._confirm_discard():
            return False
        name = s.display_name()
        self._reset_tab_selections()
        s.new_project()
        self.stack.setCurrentIndex(PAGE_WELCOME)
        self.statusBar().showMessage(f"Closed “{name}”.")
        return True

    def _on_files_changed(self) -> None:
        self._rebuild_file_tree()
        has = bool(self.session.files)
        was_welcome = self.stack.currentIndex() == PAGE_WELCOME
        self.stack.setCurrentIndex(PAGE_WORK if has else PAGE_WELCOME)
        if not has:
            self.files_dock.hide()                 # nothing to list yet: let the welcome page breathe
        elif was_welcome:
            self.files_dock.show()
        self.welcome.set_recents(self.session.recent_projects())
        self.welcome.set_current(None)             # a page change caused by loading or clearing data never shows "return"
        self.tb_home.setEnabled(has)
        self.act_home.setEnabled(has)
        self._update_close_enabled()
        for a in (self.act_pdf, self.act_xlsx, self.act_csv):
            a.setEnabled(has)
        self.export_btn.setEnabled(has)
        n_ca, n_cv = len(self.session.ca_curves()), len(self.session.cv_runs())
        n_files, n_meas = len(self.session.files), len(self.session.all_curves())
        plural = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"            # noqa: E731
        self.count_label.setText(f"{plural(n_files, 'file')}  ·  {plural(n_ca, 'CA run')}  ·  {plural(n_cv, 'CV run')}  ·  "
                                 f"{plural(n_meas, 'measurement')}   ")

    def _populate_recent_menu(self) -> None:
        """File ▸ Open recent: the most recent projects (as many as Settings allows), then Clear menu."""
        for old_action in self.recent_menu.actions():
            old_action.deleteLater()                                       # clear() only unhooks them: free them too
        self.recent_menu.clear()
        recents = self.session.recent_projects()[:prefs.get("recent_limit")]
        if not recents:
            self.recent_menu.addAction("No recent projects").setEnabled(False)
            return
        for p in recents:
            a = QAction(Path(p).name, self)
            a.setToolTip(p)
            a.setStatusTip(p)
            a.setData(p)                                                   # the project this entry opens
            a.triggered.connect(lambda _c=False, path=p: self.open_project_from(path))
            self.recent_menu.addAction(a)
        self.recent_menu.addSeparator()
        clear = QAction("Clear menu", self)
        clear.triggered.connect(lambda _c=False: (self.session.clear_recent_projects(), self._populate_recent_menu()))
        self.recent_menu.addAction(clear)

    # ================================================================== project workflow
    def ui_state(self) -> dict:
        return {"tab": self.tabs.currentIndex(), "ca": self.ca_tab.get_state(), "cv": self.cv_tab.get_state(),
                "compare": self.compare_tab.get_state(), "analysis": self.analysis_tab.get_state(),
                "electrode": self.electrode_tab.get_state(), "study": self.study_tab.get_state()}

    def apply_ui_state(self, ui: dict) -> None:
        ui = ui or {}
        self.ca_tab.set_state(ui.get("ca") or {})
        self.cv_tab.set_state(ui.get("cv") or {})
        self.compare_tab.set_state(ui.get("compare") or {})
        self.analysis_tab.set_state(ui.get("analysis") or {})
        self.electrode_tab.set_state(ui.get("electrode") or {})
        self.study_tab.set_state(ui.get("study") or {})
        tab = ui.get("tab")
        if isinstance(tab, int) and 0 <= tab < self.tabs.count():
            self.tabs.setCurrentIndex(tab)

    def _reset_tab_selections(self) -> None:
        self.cv_tab.reset()
        self.compare_tab.reset()

    def _ask_save(self) -> str:
        """Ask what to do with unsaved changes. Returns SAVE, DISCARD or CANCEL."""
        box = QMessageBox(QMessageBox.Warning, "Unsaved changes",
                          f"Save changes to “{self.session.display_name()}” before continuing?",
                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, self)
        box.setDefaultButton(QMessageBox.Save)
        return {QMessageBox.Save: SAVE, QMessageBox.Discard: DISCARD}.get(box.exec(), CANCEL)

    def _confirm_discard(self) -> bool:
        """True if it is fine to replace the current session (saving it first if the user wants)."""
        s = self.session
        if not s.dirty or not (s.files or s.project_path):
            return True
        choice = self._ask_save()
        if choice == SAVE:
            return self.save_project()
        return choice == DISCARD

    def new_project(self) -> bool:
        if not self._confirm_discard():
            return False
        self._reset_tab_selections()
        self.session.new_project()
        self.statusBar().showMessage("New project.")
        return True

    def open_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", self._last_dir(), PROJECT_FILTER)
        if path:
            self.open_project_from(path)

    def open_project_from(self, path: str | Path) -> bool:
        path = Path(path)
        try:
            data = load_project(path)               # validate first: on error nothing has changed
        except ProjectError as exc:
            QMessageBox.critical(self, "Cannot open project", str(exc))
            return False
        if not self._confirm_discard():             # only bother the user once the file is known to be good
            return False
        self._reset_tab_selections()
        ui = self.session.apply_project(data, path)
        self.apply_ui_state(ui)
        self.session.qs.setValue("lastDir", str(path.parent))
        self.statusBar().showMessage(f"Opened {path.name}: {len(data.files)} data file(s).")
        return True

    def save_project(self) -> bool:
        if self.session.project_path is None:
            return self.save_project_as()
        return self.save_project_to(self.session.project_path)

    def save_project_as(self) -> bool:
        start = Path(self._last_dir()) / f"{self.session.display_name()}{SUFFIX}"
        path, _ = QFileDialog.getSaveFileName(self, "Save project", str(start), PROJECT_FILTER)
        return self.save_project_to(path) if path else False

    def save_project_to(self, path: str | Path) -> bool:
        path = Path(path)
        if path.suffix.lower() != SUFFIX:
            path = path.with_name(path.name + SUFFIX)
        try:
            with wait_cursor():
                self.session.save_project(path, ui=self.ui_state())
        except OSError as exc:
            QMessageBox.critical(self, "Cannot save project", f"{path}\n\n{exc}")
            return False
        self.session.qs.setValue("lastDir", str(path.parent))
        self.statusBar().showMessage(f"Saved {path.name} (data, settings and plots).")
        return True

    def edit_project_info(self) -> None:
        dlg = ProjectInfoDialog(self.session.meta, self)
        if dlg.exec():
            self.session.set_meta(dlg.result_meta())

    # ================================================================== exports
    def _has_analysis(self) -> bool:
        ca, cv = analysed_runs(self.session.to_project_data())
        return bool(ca or cv)

    def export_report_pdf(self, path: str | Path) -> int:
        """Write the PDF report; returns the number of pages."""
        with wait_cursor():
            return write_pdf(build_report(self.session.to_project_data(self.ui_state())), path)

    def export_workbook(self, path: str | Path) -> list[str]:
        """Write the Excel workbook; returns the sheet names."""
        with wait_cursor():
            data = self.session.to_project_data(self.ui_state())
            return export_workbook(data, build_report(data), path)

    def export_all_results(self, folder: str | Path) -> list[Path]:
        """Write the CSV results of every analysis that has been opened into ``folder``."""
        results = collect_results(self.session.to_project_data(self.ui_state()))
        written = []
        for rel, text in results.items():
            target = Path(folder) / rel.removeprefix("results/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            written.append(target)
        return written

    def _export_done(self, title: str, message: str, path: Path) -> None:
        box = QMessageBox(QMessageBox.Information, title, message, QMessageBox.Ok, self)
        open_btn = box.addButton("Open file", QMessageBox.ActionRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _pick_export_path(self, title: str, suffix: str, filt: str) -> Optional[Path]:
        if not self._has_analysis():
            QMessageBox.information(self, "Nothing to export yet",
                                    "Open at least one chronoamperometry or voltammetry run first, so there is an "
                                    "analysis to report.")
            return None
        start = Path(self._last_dir()) / f"{self.session.display_name()}{suffix}"
        path, _ = QFileDialog.getSaveFileName(self, title, str(start), filt)
        if not path:
            return None
        p = Path(path)
        return p if p.suffix.lower() == suffix else p.with_name(p.name + suffix)

    def export_report_dialog(self) -> None:
        path = self._pick_export_path("Export report", ".pdf", "PDF report (*.pdf)")
        if path is None:
            return
        try:
            pages = self.export_report_pdf(path)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", f"{path}\n\n{exc}")
            return
        self.session.qs.setValue("lastDir", str(path.parent))
        self.statusBar().showMessage(f"Wrote {path.name} ({pages} pages).")
        self._export_done("Report exported", f"{path.name}\n{pages} pages: setup, results and plots for every analysed run.", path)

    def export_workbook_dialog(self) -> None:
        path = self._pick_export_path("Export workbook", ".xlsx", "Excel workbook (*.xlsx)")
        if path is None:
            return
        try:
            sheets = self.export_workbook(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export failed", f"{path}\n\n{exc}")
            return
        self.session.qs.setValue("lastDir", str(path.parent))
        self.statusBar().showMessage(f"Wrote {path.name} ({len(sheets)} sheets).")
        self._export_done("Workbook exported", f"{path.name}\n{len(sheets)} sheets: summary, setup and results, plots, "
                                               "raw data with editable charts.", path)

    def export_all_results_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Export all results to…", self._last_dir())
        if not d:
            return
        try:
            written = self.export_all_results(d)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        if not written:
            QMessageBox.information(self, "Export all results",
                                    "Nothing to export yet: open at least one CA or CV run first.")
        else:
            QMessageBox.information(self, "Export all results", f"Wrote {len(written)} file(s) to\n{d}")

    # ================================================================== import / routing
    TABLE_FILTER = "Tables (*.csv *.txt *.tsv *.dat *.xlsx *.xlsm);;All files (*)"

    def import_table_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import a table", self._last_dir(), self.TABLE_FILTER)
        if path:
            self.import_table_from(path)

    def import_table_from(self, path: str | Path) -> bool:
        dlg = ImportDialog(path, self)
        if not dlg.exec():
            return False
        self.session.add_datafile(dlg.datafile())
        self.session.qs.setValue("lastDir", str(Path(path).parent))
        self.statusBar().showMessage(f"Imported {Path(path).name} as {dlg.curve.kind}.")
        return True

    def open_in_analysis(self, uid: str) -> None:
        self.analysis_tab.select_measurement(uid)
        self.tabs.setCurrentWidget(self.analysis_tab)

    def clear_data(self) -> None:
        if self.session.files and QMessageBox.question(
            self, "Remove all data", "Remove all loaded data and analyses from this project?"
        ) != QMessageBox.Yes:
            return
        self._reset_tab_selections()
        self.session.clear()

    # ================================================================== loading data
    def _last_dir(self) -> str:
        return str(self.session.qs.value("lastDir", str(Path.home())))

    def open_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add PalmSens files", self._last_dir(), FILTER)
        if paths:
            self.load_paths(paths)

    def open_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Add folder with PalmSens exports", self._last_dir())
        if d:
            self.load_paths([d])

    def load_paths(self, paths: Iterable[str | Path]) -> None:
        """Open project files and add data files / folders (any mix, e.g. from a drag-and-drop)."""
        paths = [Path(p) for p in paths]
        projects = [p for p in paths if p.suffix.lower() == SUFFIX]
        data_paths = [p for p in paths if p.suffix.lower() != SUFFIX]
        if projects and not self.open_project_from(projects[0]):
            return
        if not data_paths:
            return
        loaded, errors = self.session.add_paths(data_paths)
        first = data_paths[0]
        self.session.qs.setValue("lastDir", str(first if first.is_dir() else first.parent))
        if loaded:
            n_ca = sum(len(f.of_kind(KIND_CA)) for f in loaded)
            n_cv = sum(len(f.of_kind(KIND_CV)) for f in loaded)
            n_all = sum(len(f.curves) for f in loaded)
            self.statusBar().showMessage(f"Added {len(loaded)} file(s): {n_all} measurement(s) ({n_ca} CA, {n_cv} CV scan(s)).")
            first_ca = next((c for f in loaded for c in f.of_kind(KIND_CA)), None)
            if first_ca is not None:
                self.ca_tab.select_curve(first_ca.uid)
                self.tabs.setCurrentWidget(self.ca_tab)
            else:                                   # no chronoamperometry: land on the measurement list
                self.tabs.setCurrentWidget(self.measurements_tab)
        if errors:
            QMessageBox.warning(self, "Some files could not be loaded", "\n".join(errors))

    # ================================================================== file tree
    def _rebuild_file_tree(self) -> None:
        self.file_tree.clear()
        for f in self.session.files:
            top = QTreeWidgetItem([f.name])
            top.setToolTip(0, str(f.path))
            self.file_tree.addTopLevelItem(top)
            for c in f.curves:
                tag = c.kind if c.kind != "OTHER" else "··"
                it = QTreeWidgetItem([f"{tag}  {curve_short_label(c)}"])
                it.setData(0, Qt.UserRole, c.uid)
                it.setToolTip(0, c.label)
                top.addChild(it)
            top.setExpanded(True)

    def _on_tree_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        uid = item.data(0, Qt.UserRole)
        c = self.session.curve(uid) if uid else None
        if c is None:
            return
        if c.kind == KIND_CA:
            self.ca_tab.select_curve(uid)
            self.tabs.setCurrentWidget(self.ca_tab)
        elif c.kind == KIND_CV:
            self.cv_tab.show_curve(uid)
            self.tabs.setCurrentWidget(self.cv_tab)
        else:
            self.open_in_analysis(uid)

    # ================================================================== drag & drop / close
    def dragEnterEvent(self, e) -> None:  # noqa: N802 (Qt API)
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.load_paths(paths)

    def closeEvent(self, e) -> None:  # noqa: N802
        if not self._confirm_discard():
            e.ignore()
            return
        self.session.qs.setValue("window/geometry", self.saveGeometry())
        if self.files_dock.isVisible():
            self.session.qs.setValue("layout/filesDockWidth", self.files_dock.width())
        super().closeEvent(e)

    # ================================================================== help
    def show_presets_folder(self) -> None:
        folder = self.session.presets.path.parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def show_about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("About Echem Analyzer")
        box.setIconPixmap(theme.app_logo(72))
        box.setText(f"<b style='font-size:15px'>Echem Analyzer</b> &nbsp;{__version__}")
        box.setInformativeText(
            "Standard-addition chronoamperometry and multi-cycle voltammetry for PalmSens data.<br><br>"
            "<b>Projects</b> (.echemproj) hold the raw curves, every setting, the window state and the plots in one "
            "file. <b>Reports</b> (PDF) and <b>workbooks</b> (Excel) are generated from the same analysis you see "
            "on screen.<br><br>"
            "<b>Standard addition:</b> |i| of each plateau is fitted against C = C<sub>s</sub>·ΣV/(V<sub>0</sub>+ΣV); "
            "C<sub>x</sub> = intercept/slope; LOD = 3σ/S, LOQ = 10σ/S. Background current biases C<sub>x</sub> "
            "unless a blank is subtracted.<br>"
            "<b>Voltammetry:</b> anodic peak = maximum on the positive-going sweep, cathodic = minimum on the "
            "negative-going sweep; charge = ∫ i dt; apparent capacitance (Q<sub>a</sub> − Q<sub>c</sub>)/(2ΔE) "
            "also contains any Faradaic current.")
        box.exec()

    # ================================================================== support card
    def toggle_support_card(self) -> None:
        self.support_card.toggle()

    def schedule_support_popup(self, delay_ms: int = 1800) -> None:
        """Slide the support card in shortly after the window appears (unless the user turned that off in Settings).

        Called by the program's entry point only, so tests and the self-test never see it."""
        if prefs.get("support_popup"):
            QTimer.singleShot(delay_ms, self._show_startup_card)

    def _show_startup_card(self) -> None:
        if prefs.get("support_popup") and self.isVisible() and not self.support_card.isVisible():
            self.support_card.pop()

    def _support_never(self) -> None:
        prefs.set("support_popup", False)
        self.statusBar().showMessage("The support card will not appear at startup. The coffee button stays in the toolbar; "
                                     "turn the card back on in Settings ▸ Files and storage.", 10000)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        card = getattr(self, "support_card", None)
        if card is not None:
            card.reposition()
