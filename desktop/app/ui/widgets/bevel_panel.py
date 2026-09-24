"""
Reusable Windows 95 BevelPanel widget for raised, sunken, and etched panel styling.
"""

from enum import Enum
from typing import Optional, Union
from PySide6.QtWidgets import QFrame, QWidget


class BevelStyle(Enum):
    """Bevel style types supported by BevelPanel."""
    SUNKEN = "sunken"
    RAISED = "raised"
    ETCHED = "etched"


class BevelPanel(QFrame):
    """
    A classic Windows 95 3D beveled container frame.
    
    Provides standard raised, sunken, and etched border treatments using
    Qt frame mechanisms and Windows 95 style tokens.
    """

    def __init__(
        self,
        bevel_style: Union[BevelStyle, str] = BevelStyle.SUNKEN,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.set_bevel_style(bevel_style)

    def set_bevel_style(self, style: Union[BevelStyle, str]) -> None:
        """Set the bevel style (raised, sunken, or etched)."""
        if isinstance(style, str):
            try:
                style_enum = BevelStyle(style.lower())
            except ValueError:
                style_enum = BevelStyle.SUNKEN
        else:
            style_enum = style

        self._bevel_style = style_enum

        if style_enum == BevelStyle.SUNKEN:
            self.setFrameShape(QFrame.Shape.Panel)
            self.setFrameShadow(QFrame.Shadow.Sunken)
            self.setLineWidth(1)
            self.setMidLineWidth(1)
            self.setProperty("bevelStyle", "sunken")
            self.setObjectName("BevelPanel-sunken")
        elif style_enum == BevelStyle.RAISED:
            self.setFrameShape(QFrame.Shape.Panel)
            self.setFrameShadow(QFrame.Shadow.Raised)
            self.setLineWidth(1)
            self.setMidLineWidth(1)
            self.setProperty("bevelStyle", "raised")
            self.setObjectName("BevelPanel-raised")
        elif style_enum == BevelStyle.ETCHED:
            self.setFrameShape(QFrame.Shape.Box)
            self.setFrameShadow(QFrame.Shadow.Sunken)
            self.setLineWidth(1)
            self.setMidLineWidth(0)
            self.setProperty("bevelStyle", "etched")
            self.setObjectName("BevelPanel-etched")

        # Refresh style sheet evaluation for dynamic property
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    @property
    def bevel_style(self) -> BevelStyle:
        """Return current bevel style."""
        return self._bevel_style
