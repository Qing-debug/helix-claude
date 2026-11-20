"""The Windows virtual-key codes of the bot's keys (C2), as plain data.

listener.py and the offline checkers (analyze.py, and realism.py through it) both use these. They live here, not in
listener.py, because listener.py calls ctypes.WinDLL at import time and the offline checkers must import without
touching Windows (W0-4).
"""
from src.input.protocol import Key

# C2: the Windows VK each bot key arrives as in the LL hook (values confirmed against
# learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes, 2026-10-22).
VK_BY_KEY: dict[Key, int] = {
    Key.LEFT: 0x25,   # VK_LEFT
    Key.RIGHT: 0x27,  # VK_RIGHT
    Key.UP: 0x26,     # VK_UP
    Key.DOWN: 0x28,   # VK_DOWN
    Key.E: 0x45,      # E key
    Key.ALT: 0xA4,    # VK_LMENU (left Alt; arrives as WM_SYSKEYDOWN/UP)
    Key.SPACE: 0x20,  # VK_SPACE
}
BOT_VKS: frozenset[int] = frozenset(VK_BY_KEY.values())
