DARK_QSS = """
/* ── Base ── */
QMainWindow { background-color: #1a1a2e; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
QWidget { background: transparent; color: #e0e0e0; }

/* ── Group boxes (sidebar cards) ── */
QGroupBox { background-color: #16213e; border: 1px solid #0f3460; border-radius: 10px; margin-top: 14px; padding: 15px 10px 10px 10px; font-weight: bold; color: #e94560; }
QGroupBox::title { subcontrol-origin: margin; left: 15px; padding: 0 8px; color: #e94560; }

/* ── Labels ── */
QLabel { background: transparent; color: #c4c4c4; }
QLabel#titleLabel { font-size: 20px; font-weight: bold; color: #ffffff; }
QLabel#sectionLabel { font-weight: bold; color: #e0e0e0; }
QLabel#valueLabel { color: #00d2ff; font-weight: bold; }
QLabel#statusOk { color: #00e676; font-weight: bold; }
QLabel#statusWarn { color: #ff9800; }
QLabel#statusError { color: #ff1744; font-weight: bold; }
QLabel#summaryLabel { color: #90caf9; font-style: italic; }
QLabel#cardTitle { color: #00d2ff; font-size: 14px; font-weight: bold; }
QLabel#errorLabel { color: #ff1744; font-size: 16px; font-weight: bold; }
QLabel#warnLabel { color: #ff9800; font-size: 16px; font-weight: bold; }

/* Badge labels — these DO need an opaque background */
QLabel#badgeAvail { background-color: #00e676; color: #1a1a2e; font-weight: bold; font-size: 11px; border-radius: 4px; padding: 2px 8px; }
QLabel#badgeOccupied { background-color: #ff1744; color: #ffffff; font-weight: bold; font-size: 11px; border-radius: 4px; padding: 2px 8px; }
QLabel#badgeAC { background-color: #2979ff; color: white; font-size: 11px; font-weight: bold; border-radius: 4px; padding: 2px 6px; }
QLabel#badgeDC { background-color: #ff9800; color: #1a1a2e; font-size: 11px; font-weight: bold; border-radius: 4px; padding: 2px 6px; }

/* ── Combo boxes ── */
QComboBox { background-color: #0f3460; color: #e0e0e0; border: 1px solid #1a1a5e; border-radius: 8px; padding: 7px 12px; min-height: 22px; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow { image: none; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 6px solid #e94560; margin-right: 8px; }
QComboBox QAbstractItemView { background-color: #16213e; color: #e0e0e0; selection-background-color: #e94560; border: 1px solid #0f3460; border-radius: 4px; }

/* ── Sliders ── */
QSlider::groove:horizontal { height: 6px; background: #0f3460; border-radius: 3px; }
QSlider::handle:horizontal { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #e94560, stop:1 #00d2ff); width: 18px; height: 18px; margin: -7px 0; border-radius: 9px; }
QSlider::sub-page:horizontal { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #e94560, stop:1 #00d2ff); border-radius: 3px; }

/* ── Buttons ── */
QPushButton#primaryBtn { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #e94560, stop:1 #c62a47); color: white; font-size: 15px; font-weight: bold; border: none; border-radius: 10px; padding: 14px; }
QPushButton#primaryBtn:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #ff6b81, stop:1 #e94560); }
QPushButton#primaryBtn:disabled { background: #333; color: #666; }
QPushButton#secondaryBtn { background-color: #0f3460; color: #c4c4c4; font-size: 13px; font-weight: bold; border: 1px solid #1a1a5e; border-radius: 8px; padding: 10px; }
QPushButton#secondaryBtn:hover { background-color: #1a1a5e; color: #ffffff; }

/* ── Radio buttons ── */
QRadioButton { color: #c4c4c4; spacing: 8px; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 8px; border: 2px solid #0f3460; background: #1a1a2e; }
QRadioButton::indicator:checked { background: #e94560; border-color: #e94560; }

/* ── Scroll area ── */
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #1a1a2e; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #0f3460; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

/* ── Station cards ── */
QFrame#stationCard { background-color: #16213e; border: 1px solid #0f3460; border-radius: 12px; }
QFrame#stationCard:hover { border-color: #e94560; }

/* ── Splitter handle ── */
QSplitter::handle { background: #0f3460; width: 2px; }
"""
