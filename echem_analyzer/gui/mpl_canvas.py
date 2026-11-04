"""Matplotlib-in-Qt helpers: a plot widget with toolbar and a draggable step-marker editor."""
from __future__ import annotations

import os
from typing import Callable, Sequence

os.environ.setdefault("QT_API", "pyside6")

import matplotlib  # noqa: E402

matplotlib.use("QtAgg")

import numpy as np  # noqa: E402
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtWidgets import QVBoxLayout, QWidget  # noqa: E402

from .. import theme  # noqa: E402

class _Toolbar(NavigationToolbar2QT):
    """matplotlib's navigation toolbar without its generic subplot / axis-editor tools."""

    toolitems = [t for t in NavigationToolbar2QT.toolitems
                 if t[0] in (None, "Home", "Back", "Forward", "Pan", "Zoom", "Save")]


class _Canvas(FigureCanvasQTAgg):
    """Ignores resize events to a degenerate size.

    A canvas on a hidden page (e.g. the tabs behind the welcome page) is resized to 0x0; matplotlib would
    then collapse the axes and fail to invert their transform when something is drawn.  Keeping the last
    good size lets data be drawn while hidden, and the real resize on show redraws it properly.
    """

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        size = event.size()
        if size.width() < 2 or size.height() < 2:
            return
        super().resizeEvent(event)


class PlotWidget(QWidget):
    """A matplotlib canvas with the standard navigation toolbar (zoom / pan / save)."""

    def __init__(self, parent=None, figsize=(6.0, 4.0), toolbar: bool = True):
        super().__init__(parent)
        theme.apply_mpl(theme.current())
        self.fig = Figure(figsize=figsize, layout="constrained")
        self.canvas = _Canvas(self.fig)
        self.toolbar = _Toolbar(self.canvas, self) if toolbar else None
        if self.toolbar is not None:
            self.toolbar.setIconSize(QSize(17, 17))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if self.toolbar is not None:
            lay.addWidget(self.toolbar)
        lay.addWidget(self.canvas, 1)
        theme.bus.changed.connect(self._on_theme)

    def _on_theme(self) -> None:
        """Re-colour the figure background; the owner redraws the axes (they read rcParams / the palette)."""
        self.fig.set_facecolor(theme.current().plot_bg)
        self.canvas.draw_idle()

    def reset_toolbar(self) -> None:
        """Forget the zoom/pan history (call after loading different data)."""
        if self.toolbar is not None:
            self.toolbar.update()

    def toolbar_active(self) -> bool:
        if self.toolbar is None:
            return False
        mode = self.toolbar.mode
        return (getattr(mode, "value", mode) or "") != ""

    def redraw(self) -> None:
        self.canvas.draw_idle()


class StepLineEditor:
    """Mouse editing of vertical step markers on one axes.

    * drag a marker            -> ``on_move(index, new_time)``
    * double-click empty space -> ``on_add(time)``
    * right-click a marker     -> ``on_remove(index)``

    Interaction is ignored while the toolbar's zoom / pan tool is active.  Hit testing is
    done in pixel space against the *current* step times, so the markers can be redrawn
    freely by the owner after every change.
    """

    def __init__(
        self,
        plot: PlotWidget,
        ax,
        get_steps: Callable[[], Sequence[float]],
        on_move: Callable[[int, float], None],
        on_add: Callable[[float], None],
        on_remove: Callable[[int], None],
        tol_px: float = 7.0,
    ):
        self.plot, self.ax = plot, ax
        self.get_steps, self.on_move, self.on_add, self.on_remove = get_steps, on_move, on_add, on_remove
        self.tol_px = tol_px
        self._drag_idx: int | None = None
        self._ghost = None
        c = plot.canvas
        c.mpl_connect("button_press_event", self._press)
        c.mpl_connect("motion_notify_event", self._motion)
        c.mpl_connect("button_release_event", self._release)

    # -- helpers ---------------------------------------------------------------------
    def _hit(self, event) -> int | None:
        steps = list(self.get_steps())
        if not steps:
            return None
        pix = self.ax.transData.transform(np.column_stack([steps, np.zeros(len(steps))]))[:, 0]
        d = np.abs(pix - event.x)
        j = int(np.argmin(d))
        return j if d[j] <= self.tol_px else None

    def _x_of(self, event) -> float:
        return float(self.ax.transData.inverted().transform((event.x, event.y))[0])

    # -- event handlers --------------------------------------------------------------
    def _press(self, event) -> None:
        if event.inaxes is not self.ax or self.plot.toolbar_active():
            return
        if event.button == 1 and getattr(event, "dblclick", False):
            self.on_add(float(event.xdata))
            return
        idx = self._hit(event)
        if idx is None:
            return
        if event.button == 3:
            self.on_remove(idx)
        elif event.button == 1:
            self._drag_idx = idx
            x = float(self.get_steps()[idx])
            self._ghost = self.ax.axvline(x, color=theme.current().text, lw=1.6, alpha=0.8, zorder=10)
            self.plot.redraw()

    def _motion(self, event) -> None:
        if self._drag_idx is None or self._ghost is None or event.x is None:
            return
        x = self._x_of(event)
        self._ghost.set_xdata([x, x])
        self.plot.redraw()

    def _release(self, event) -> None:
        if self._drag_idx is None:
            return
        idx, ghost = self._drag_idx, self._ghost
        self._drag_idx, self._ghost = None, None
        x = float(ghost.get_xdata()[0]) if ghost is not None else None
        if ghost is not None:
            try:
                ghost.remove()
            except (ValueError, NotImplementedError):
                pass
        if x is not None and abs(x - float(self.get_steps()[idx])) > 1e-9:
            self.on_move(idx, x)
        else:
            self.plot.redraw()
