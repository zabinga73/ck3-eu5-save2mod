"""PySide6 front end for the converter."""
from __future__ import annotations

import csv
import json
import os
import sys
import traceback

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSlider, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from . import __version__
from . import paths as P
from .convert import Options
from .mappings import PKG_DATA, tables_dir
from .writer import DEFAULT_GAME_VERSION, OLD_GAME_VERSION_DEFAULTS

SETTINGS = os.path.join(os.path.dirname(tables_dir()), "settings.json")


def load_settings() -> dict:
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_settings(d: dict) -> None:
    try:
        with open(SETTINGS, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
    except OSError:
        pass


# ------------------------------------------------------------------ worker
class Worker(QObject):
    log = Signal(str)
    progress = Signal(float, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kw):
        super().__init__()
        self.fn, self.args, self.kw = fn, args, kw

    def run(self):
        try:
            res = self.fn(*self.args, log=self.log.emit, **self.kw)
            self.done.emit(res)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")


def run_in_thread(owner, worker: Worker):
    th = QThread(owner)
    worker.moveToThread(th)
    th.started.connect(worker.run)
    worker.done.connect(th.quit)
    worker.failed.connect(th.quit)
    th.finished.connect(th.deleteLater)
    owner._threads = getattr(owner, "_threads", []) + [(th, worker)]
    th.start()
    return th


# ------------------------------------------------------------ path picker
class PathRow(QWidget):
    def __init__(self, kind: str, detect=None, file_filter: str = ""):
        super().__init__()
        self.kind = kind
        self.detect = detect
        self.filter = file_filter
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        b = QPushButton("Browse…")
        b.clicked.connect(self.browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(b)
        if detect:
            d = QPushButton("Detect")
            d.clicked.connect(self.autodetect)
            lay.addWidget(d)

    def text(self) -> str:
        return self.edit.text().strip()

    def set(self, v: str | None):
        if v:
            self.edit.setText(v)

    def autodetect(self):
        v = self.detect() if self.detect else None
        if v:
            self.edit.setText(v)
        else:
            QMessageBox.information(self, "Not found", "Could not find it automatically - please browse to it.")

    def browse(self):
        start = self.text() or os.path.expanduser("~")
        if self.kind == "file":
            p, _ = QFileDialog.getOpenFileName(self, "Choose file", os.path.dirname(start), self.filter)
        else:
            p = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        if p:
            self.edit.setText(p)


# ------------------------------------------------------------ table editor
class TableEditor(QWidget):
    def __init__(self, filename: str, help_text: str):
        super().__init__()
        self.filename = filename
        lay = QVBoxLayout(self)
        info = QLabel(help_text)
        info.setWordWrap(True)
        lay.addWidget(info)
        filt = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter rows…")
        self.search.textChanged.connect(self.apply_filter)
        filt.addWidget(self.search)
        lay.addLayout(filt)
        self.table = QTableWidget()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        btns = QHBoxLayout()
        for text, fn in (("Add row", self.add_row), ("Delete row", self.del_row), ("Save", self.save),
                         ("Reset to defaults", self.reset)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch(1)
        self.where = QLabel()
        btns.addWidget(self.where)
        lay.addLayout(btns)
        self.comments: list[str] = []
        self.load()

    def _path(self) -> str:
        user = os.path.join(tables_dir(), self.filename)
        return user if os.path.exists(user) else os.path.join(PKG_DATA, self.filename)

    def load(self):
        path = self._path()
        self.comments = []
        rows: list[list[str]] = []
        header: list[str] = []
        with open(path, encoding="utf-8-sig", newline="") as fh:
            lines = []
            for ln in fh:
                if ln.lstrip().startswith("#"):
                    self.comments.append(ln.rstrip("\n"))
                elif ln.strip():
                    lines.append(ln)
        rd = list(csv.reader(lines))
        if rd:
            header, rows = rd[0], rd[1:]
        self.table.clear()
        self.table.setColumnCount(len(header))
        self.table.setHorizontalHeaderLabels(header)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c in range(len(header)):
                self.table.setItem(r, c, QTableWidgetItem(row[c] if c < len(row) else ""))
        self.table.resizeColumnsToContents()
        for c in range(self.table.columnCount()):
            self.table.setColumnWidth(c, min(self.table.columnWidth(c), 380))
        self.where.setText("editing your copy" if path.startswith(tables_dir()) else "showing built-in defaults")

    def apply_filter(self, text: str):
        t = text.lower()
        for r in range(self.table.rowCount()):
            show = not t or any(t in (self.table.item(r, c).text().lower() if self.table.item(r, c) else "")
                                for c in range(self.table.columnCount()))
            self.table.setRowHidden(r, not show)

    def add_row(self):
        self.table.insertRow(self.table.rowCount())
        self.table.scrollToBottom()

    def del_row(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def save(self):
        path = os.path.join(tables_dir(), self.filename)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            for c in self.comments:
                fh.write(c + "\n")
            w = csv.writer(fh)
            w.writerow([self.table.horizontalHeaderItem(c).text() for c in range(self.table.columnCount())])
            for r in range(self.table.rowCount()):
                vals = [(self.table.item(r, c).text() if self.table.item(r, c) else "").strip()
                        for c in range(self.table.columnCount())]
                if any(vals):
                    w.writerow(vals)
        self.where.setText("saved - used by the next conversion")

    def reset(self):
        user = os.path.join(tables_dir(), self.filename)
        if os.path.exists(user):
            if QMessageBox.question(self, "Reset", "Discard your edits to this table?") != QMessageBox.Yes:
                return
            os.remove(user)
        self.load()


# ------------------------------------------------------------ preview
class PreviewTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Zoom"))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(50, 400)
        self.zoom.setValue(100)
        self.zoom.valueChanged.connect(self._apply)
        top.addWidget(self.zoom, 1)
        self.open_btn = QPushButton("Open mod folder")
        self.open_btn.clicked.connect(self.open_folder)
        self.open_btn.setEnabled(False)
        top.addWidget(self.open_btn)
        lay.addLayout(top)
        self.scroll = QScrollArea()
        self.label = QLabel("Run a conversion to see the resulting map here.")
        self.label.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.label)
        self.scroll.setWidgetResizable(False)
        lay.addWidget(self.scroll, 1)
        self.pix = None
        self.folder = None

    def show_result(self, png: str | None, folder: str):
        self.folder = folder
        self.open_btn.setEnabled(True)
        if png and os.path.exists(png):
            self.pix = QPixmap(png)
            self._apply()

    def _apply(self):
        if self.pix is None:
            return
        f = self.zoom.value() / 100.0
        p = self.pix.scaled(int(self.pix.width() * f), int(self.pix.height() * f), Qt.KeepAspectRatio,
                            Qt.FastTransformation)
        self.label.setPixmap(p)
        self.label.resize(p.size())

    def open_folder(self):
        if self.folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder))


# ------------------------------------------------------------ main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"CK3 EU5 Save-2-Mod {__version__}")
        self.resize(1100, 780)
        self.settings = load_settings()
        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        tabs.addTab(self._convert_tab(), "Convert")
        maps = QTabWidget()
        maps.addTab(TableEditor("culture_map.csv",
                                "CK3 culture → EU5 culture. 'regional' lists EU5 cultures (or group:<group>) that are "
                                "kept when a location's vanilla culture is among them, so CK3 'German' stays Bavarian "
                                "in Bavaria. Cultures not listed are matched automatically by name."), "Cultures")
        maps.addTab(TableEditor("religion_map.csv",
                                "CK3 faith (or whole religion) → EU5 religion. Custom/reformed faiths fall back to the "
                                "faith they came from, then to their religion row."), "Religions")
        maps.addTab(TableEditor("building_map.csv",
                                "CK3 building family → EU5 building, for towns (eu5) and rural settlements (eu5_rural). "
                                "EU5 level = ceil(CK3 level / level_div), capped at max_level; CK3 levels below "
                                "min_ck3_level are ignored."), "Buildings")
        self.realms = TableEditor("title_tags.csv",
                                  "Every realm from the last conversion is listed here. eu5_tag: blank = automatic "
                                  "(matched by English name), an EU5 tag forces it, '-' forces a new tag. name / "
                                  "adjective: type to rename the country in EU5. last_conversion shows what the last "
                                  "run did. Save, then convert again.")
        maps.addTab(self.realms, "Realms && tags")
        tabs.addTab(maps, "Mappings")
        self.preview = PreviewTab()
        tabs.addTab(self.preview, "Map preview")
        self.tabs = tabs
        self._fill_defaults()

    # ----------------------------------------------------------- UI build
    def _convert_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("Files")
        f = QFormLayout(g)
        self.ck3 = PathRow("dir", P.find_ck3_game)
        self.eu5 = PathRow("dir", P.find_eu5_game)
        self.save = PathRow("file", lambda: P.newest_save(P.find_ck3_saves()), "CK3 saves (*.ck3);;All files (*)")
        self.out = PathRow("dir", P.find_eu5_mod_dir)
        self.name = QLineEdit("CK3 Conversion")
        f.addRow("CK3 game folder", self.ck3)
        f.addRow("EU5 game folder", self.eu5)
        f.addRow("CK3 save file", self.save)
        f.addRow("EU5 mod folder", self.out)
        f.addRow("Mod name", self.name)
        lay.addWidget(g)

        o = QGroupBox("Options")
        grid = QGridLayout(o)
        self.keep_bld = QCheckBox("Keep base EU5 buildings in converted land")
        self.all_pops = QCheckBox("All pops take the CK3 county's culture && religion (off: majority group only)")
        self.control = QCheckBox("Transfer CK3 county control")
        self.reuse = QCheckBox("Reuse matching EU5 tags (flags, names, missions)")
        self.hre = QCheckBox("Rebuild the Holy Roman Empire if the save has one")
        self.hre_counts = QCheckBox("Emperor's direct vassal counts become independent HRE members")
        for i, cb in enumerate((self.keep_bld, self.all_pops, self.control, self.reuse, self.hre, self.hre_counts)):
            cb.setChecked(True)
            grid.addWidget(cb, i // 2, i % 2)
        row = 3
        grid.addWidget(QLabel("Buildings of a barony go to"), row, 0)
        self.placement = QComboBox()
        self.placement.addItems(["every EU5 location it covers", "only its main EU5 location"])
        grid.addWidget(self.placement, row, 1)
        grid.addWidget(QLabel("Vassals become EU5 subjects from tier"), row + 1, 0)
        self.sub_tier = QComboBox()
        for label, tier in (("kingdom", 3), ("duchy", 2), ("county", 1), ("empire", 4)):
            self.sub_tier.addItem(label, tier)
        grid.addWidget(self.sub_tier, row + 1, 1)
        grid.addWidget(QLabel("Development multiplier (CK3 → EU5)"), row + 2, 0)
        self.dev_mult = QDoubleSpinBox()
        self.dev_mult.setRange(0.1, 5.0)
        self.dev_mult.setSingleStep(0.1)
        self.dev_mult.setValue(1.0)
        grid.addWidget(self.dev_mult, row + 2, 1)
        grid.addWidget(QLabel("Mod targets EU5 version"), row + 3, 0)
        self.game_version = QLineEdit(DEFAULT_GAME_VERSION)
        self.game_version.setToolTip("Written to the mod's metadata so the launcher doesn't flag it as outdated")
        grid.addWidget(self.game_version, row + 3, 1)
        self.chars = QCheckBox("Convert rulers, consorts && heirs (off = EU5 generates random rulers)")
        self.chars.setChecked(True)
        grid.addWidget(self.chars, row + 4, 0, 1, 2)
        self.admin_whole = QCheckBox("Administrative realms (Byzantium, China…) keep their governors' land")
        self.admin_whole.setToolTip("Vassals with CK3 administrative/celestial government under such a realm are "
                                    "merged into it instead of becoming subjects")
        self.admin_whole.setChecked(True)
        grid.addWidget(self.admin_whole, row + 5, 0, 1, 2)
        self.exclaves = QCheckBox("Detached parts of a country become its vassals (EU5-generated rulers)")
        self.exclaves.setToolTip("Land not connected to the capital's part by land, a strait or a short sea crossing")
        self.exclaves.setChecked(True)
        grid.addWidget(self.exclaves, row + 6, 0)
        hop = QHBoxLayout()
        hop.addWidget(QLabel("…connected across up to"))
        self.sea_hops = QSpinBox()
        self.sea_hops.setRange(0, 10)
        self.sea_hops.setValue(2)
        self.sea_hops.setToolTip("How many sea/lake/wasteland locations a crossing may pass and still count as "
                                 "connected (EU5 straits always count)")
        hop.addWidget(self.sea_hops)
        hop.addWidget(QLabel("sea locations"))
        hop.addStretch(1)
        grid.addLayout(hop, row + 6, 1)
        self.pockets = QCheckBox("Fill pockets: EU5 land the alignment missed but CK3 land surrounds joins it")
        self.pockets.setToolTip("Alpine valleys that are CK3 mountains, slivers along borders… up to 40 locations "
                                "per pocket; each takes the CK3 barony it borders most")
        self.pockets.setChecked(True)
        grid.addWidget(self.pockets, row + 7, 0, 1, 2)
        self.title_names = QCheckBox("Use CK3's displayed title names (historical names like West Francia, renames)")
        self.title_names.setToolTip("New countries take the name CK3 shows; reused EU5 countries only when it "
                                    "differs from CK3's default name for the title")
        grid.addWidget(self.title_names, row + 8, 0, 1, 2)
        self.recompute_map = QCheckBox("Recompute map alignment (ignore cache)")
        grid.addWidget(self.recompute_map, row + 9, 0)
        lay.addWidget(o)

        btns = QHBoxLayout()
        self.inspect_btn = QPushButton("Inspect save…")
        self.inspect_btn.setToolTip("Print the save's structure to the log (useful if a conversion fails)")
        self.inspect_btn.clicked.connect(self.inspect)
        self.check_btn = QPushButton("Check mod…")
        self.check_btn.setToolTip("Check an already generated mod folder against the rules vanilla EU5's setup follows")
        self.check_btn.clicked.connect(self.check_mod)
        self.go = QPushButton("Convert")
        self.go.setMinimumHeight(36)
        f2 = QFont()
        f2.setBold(True)
        self.go.setFont(f2)
        self.go.clicked.connect(self.convert)
        btns.addWidget(self.inspect_btn)
        btns.addWidget(self.check_btn)
        btns.addStretch(1)
        btns.addWidget(self.go)
        lay.addLayout(btns)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.status = QLabel("")
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        self.logv = QPlainTextEdit()
        self.logv.setReadOnly(True)
        self.logv.setFont(QFont("monospace"))
        lay.addWidget(self.logv, 1)
        return w

    def _fill_defaults(self):
        s = self.settings
        self.ck3.set(s.get("ck3") or P.find_ck3_game())
        self.eu5.set(s.get("eu5") or P.find_eu5_game())
        self.save.set(s.get("save") or P.newest_save(P.find_ck3_saves()))
        self.out.set(s.get("out") or P.find_eu5_mod_dir())
        self.name.setText(s.get("name", "CK3 Conversion"))
        for key, cb in (("keep_bld", self.keep_bld), ("all_pops", self.all_pops), ("control", self.control),
                        ("reuse", self.reuse), ("hre", self.hre), ("hre_counts", self.hre_counts)):
            if key in s:
                cb.setChecked(bool(s[key]))
        self.placement.setCurrentIndex(s.get("placement", 0))
        # "subject_tier" (0.1.0+) stores the tier itself; the old index setting is ignored
        idx = self.sub_tier.findData(int(s.get("subject_tier", 3)))
        self.sub_tier.setCurrentIndex(idx if idx >= 0 else 0)
        self.admin_whole.setChecked(bool(s.get("admin_whole", True)))
        self.exclaves.setChecked(bool(s.get("exclaves", True)))
        self.sea_hops.setValue(int(s.get("sea_hops", 2)))
        self.pockets.setChecked(bool(s.get("pockets", True)))
        self.dev_mult.setValue(s.get("dev_mult", 1.0))
        gv = s.get("game_version", "")
        self.game_version.setText(gv if gv and gv not in OLD_GAME_VERSION_DEFAULTS else DEFAULT_GAME_VERSION)
        self.chars.setChecked(bool(s.get("chars", True)))
        self.title_names.setChecked(bool(s.get("title_names", False)))

    def _store(self):
        self.settings.update({
            "ck3": self.ck3.text(), "eu5": self.eu5.text(), "save": self.save.text(), "out": self.out.text(),
            "name": self.name.text(), "keep_bld": self.keep_bld.isChecked(), "all_pops": self.all_pops.isChecked(),
            "control": self.control.isChecked(), "reuse": self.reuse.isChecked(), "hre": self.hre.isChecked(),
            "hre_counts": self.hre_counts.isChecked(), "placement": self.placement.currentIndex(),
            "subject_tier": int(self.sub_tier.currentData()), "dev_mult": self.dev_mult.value(),
            "admin_whole": self.admin_whole.isChecked(), "exclaves": self.exclaves.isChecked(),
            "sea_hops": self.sea_hops.value(), "pockets": self.pockets.isChecked(),
            "game_version": self.game_version.text().strip(), "chars": self.chars.isChecked(),
            "title_names": self.title_names.isChecked()})
        save_settings(self.settings)

    def options(self) -> Options:
        return Options(
            keep_vanilla_buildings=self.keep_bld.isChecked(), all_pops_take_ck3=self.all_pops.isChecked(),
            transfer_control=self.control.isChecked(), reuse_tags=self.reuse.isChecked(),
            rebuild_hre=self.hre.isChecked(), hre_direct_counts_independent=self.hre_counts.isChecked(),
            building_placement="all" if self.placement.currentIndex() == 0 else "main",
            subject_min_tier=int(self.sub_tier.currentData()),
            keep_admin_realms_whole=self.admin_whole.isChecked(), split_exclaves=self.exclaves.isChecked(),
            fill_enclaves=self.pockets.isChecked(),
            exclave_sea_hop=self.sea_hops.value(),
            dev_multiplier=self.dev_mult.value(), mod_name=self.name.text().strip() or "CK3 Conversion",
            convert_characters=self.chars.isChecked(), game_version=self.game_version.text().strip(),
            ck3_title_names=self.title_names.isChecked())

    # ------------------------------------------------------------ actions
    @Slot(str)
    def append(self, s: str):
        self.logv.appendPlainText(s)

    @Slot(float, str)
    def _on_progress(self, f: float, msg: str):
        self.bar.setValue(int(f * 1000))
        self.status.setText(msg)

    @Slot(object)
    def _inspect_done(self, _r):
        self.set_busy(False)

    def set_busy(self, busy: bool):
        self.go.setEnabled(not busy)
        self.inspect_btn.setEnabled(not busy)
        self.check_btn.setEnabled(not busy)

    def convert(self):
        missing = [n for n, r in (("CK3 game folder", self.ck3), ("EU5 game folder", self.eu5),
                                  ("CK3 save file", self.save), ("EU5 mod folder", self.out)) if not r.text()]
        if missing:
            QMessageBox.warning(self, "Missing", "Please fill in: " + ", ".join(missing))
            return
        self._store()
        from .pipeline import Paths, run_conversion
        self.logv.clear()
        self.set_busy(True)
        paths = Paths(self.ck3.text(), self.eu5.text(), self.save.text(), self.out.text())
        wk = Worker(run_conversion, paths, self.options(), use_map_cache=not self.recompute_map.isChecked())
        wk.kw["progress"] = lambda f, msg="": wk.progress.emit(f, msg)
        # every slot is a method of this window so Qt runs it on the GUI thread
        wk.log.connect(self.append, Qt.QueuedConnection)
        wk.progress.connect(self._on_progress, Qt.QueuedConnection)
        wk.done.connect(self._converted, Qt.QueuedConnection)
        wk.failed.connect(self._failed, Qt.QueuedConnection)
        run_in_thread(self, wk)

    @Slot(object)
    def _converted(self, res):
        self.set_busy(False)
        self.append(f"\nFinished in {res.seconds:.0f}s. Mod: {res.mod_root}")
        self.append("Enable it in the EU5 launcher's playset, then start a new game (1337 start).")
        self.preview.show_result(res.preview_png, res.mod_root)
        self.realms.load()
        if res.preview_png:
            self.tabs.setCurrentWidget(self.preview)

    @Slot(str)
    def _failed(self, msg: str):
        self.set_busy(False)
        self.append("\nERROR: " + msg)
        QMessageBox.critical(self, "Conversion failed", msg.split("\n\n")[0])

    def inspect(self):
        if not self.save.text():
            return
        from .ck3save import summarize_structure

        def job(path, log):
            summarize_structure(path, out=log)
            return None
        self.logv.clear()
        self.set_busy(True)
        wk = Worker(job, self.save.text())
        wk.log.connect(self.append, Qt.QueuedConnection)
        wk.done.connect(self._inspect_done, Qt.QueuedConnection)
        wk.failed.connect(self._failed, Qt.QueuedConnection)
        run_in_thread(self, wk)

    def check_mod(self):
        if not self.eu5.text():
            QMessageBox.warning(self, "Missing", "Please fill in the EU5 game folder first.")
            return
        mod = QFileDialog.getExistingDirectory(self, "Generated mod folder", self.out.text() or os.path.expanduser("~"))
        if not mod:
            return
        from .eu5game import load_eu5_game
        from .pipeline import self_check

        def job(eu5_dir, mod_dir, log):
            eu5g = load_eu5_game(eu5_dir, log)
            self_check(mod_dir, eu5g, log)
            return None
        self.logv.clear()
        self.set_busy(True)
        wk = Worker(job, self.eu5.text(), mod)
        wk.log.connect(self.append, Qt.QueuedConnection)
        wk.done.connect(self._inspect_done, Qt.QueuedConnection)
        wk.failed.connect(self._failed, Qt.QueuedConnection)
        run_in_thread(self, wk)


def _install_crash_log() -> str:
    """Write Python errors and hard crashes (segfaults) to ~/.save2mod/crash.log
    so a window that just disappears still leaves a trace."""
    import faulthandler
    import threading
    path = os.path.join(os.path.dirname(tables_dir()), "crash.log")
    fh = open(path, "a", encoding="utf-8")
    fh.write(f"\n=== save2mod {__version__} started ===\n")
    fh.flush()
    faulthandler.enable(fh, all_threads=True)

    def hook(tp, val, tb):
        fh.write("".join(traceback.format_exception(tp, val, tb)))
        fh.flush()
        sys.__excepthook__(tp, val, tb)
    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)
    main._crash_fh = fh  # keep it open
    return path


def main() -> int:
    crash = _install_crash_log()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    w.append(f"save2mod {__version__}. If the window ever closes unexpectedly, details go to {crash}")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
