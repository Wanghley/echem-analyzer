"""CV tab: the multi-cycle analysis workspace and the free overlay, side by side."""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from .cv_cycle_tab import CVCycleTab
from .cv_overlay_tab import CVOverlayTab
from .session import Session


class CVTab(QWidget):
    def __init__(self, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self.cycles = CVCycleTab(session)
        self.overlay = CVOverlayTab(session)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.cycles, "Cycle analysis")
        self.tabs.addTab(self.overlay, "Overlay")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.tabs)

    def show_curve(self, uid: str) -> None:
        """Jump to a CV cycle: select its run in the cycle analysis and tick it in the overlay."""
        self.overlay.check_curve(uid)
        self.cycles.select_run_of_curve(uid)
        self.tabs.setCurrentWidget(self.cycles)

    # ---- project state
    def get_state(self) -> dict:
        return {"sub": self.tabs.currentIndex(), "cycles": self.cycles.get_state(),
                "overlay": self.overlay.get_state()}

    def set_state(self, state: dict) -> None:
        state = state or {}
        self.cycles.set_state(state.get("cycles") or {})
        self.overlay.set_state(state.get("overlay") or {})
        sub = state.get("sub")
        if isinstance(sub, int) and 0 <= sub < self.tabs.count():
            self.tabs.setCurrentIndex(sub)

    def reset(self) -> None:
        self.overlay.reset()
