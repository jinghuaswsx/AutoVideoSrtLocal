"""EZ Product Translate RPA via pyautogui.

Workflow:
    1. ensure Chrome maximized + topmost on EZ page
    2. cv2 模板匹配找当前 viewport 内所有 "+ Edit Translations" 按钮
    3. for each visible button -> open dialog -> add target lang -> upload -> save
    4. 处理完当前 viewport 后 scroll down 继续，直到所有 pairs 处理完

The dialog inner element layout (Add Language dropdown, Italian option, Save)
is currently hardcoded for chrome maximized at 3840x2160 主屏. If your screen
DPI / resolution differs, those still need calibration.
"""
from __future__ import annotations

import ctypes
import pathlib
import time

import cv2
import numpy as np
import pyautogui
import win32api
import win32clipboard
import win32con
import win32gui
from PIL import ImageGrab

# 始终开启 PyAutoGUI fail-safe：用户把鼠标快速推到屏幕任一角即可中断脚本
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05  # 每个 pyautogui 动作之间最小 50ms


# ---------- ESC 立即中断 ----------
class AbortSignal(RuntimeError):
    """用户按 ESC 中断 RPA。"""


_VK_ESCAPE = 0x1B


def check_abort() -> None:
    """如果用户按住或刚按下 ESC，立刻 raise AbortSignal。"""
    state = win32api.GetAsyncKeyState(_VK_ESCAPE)
    # 高位 = 当前按下；低位 = 上次查询后被按过
    if state & 0x8000 or state & 0x0001:
        raise AbortSignal("ESC pressed by user")


def abortable_sleep(seconds: float) -> None:
    """可中断的 sleep：每 50ms 检查一次 ESC。"""
    deadline = time.time() + seconds
    while True:
        check_abort()
        remain = deadline - time.time()
        if remain <= 0:
            return
        time.sleep(min(remain, 0.05))


# ---------- Coordinate table (Chrome maximized 3840x2160, primary monitor) ----------

# 9 个 Shopify 图片对应的 "+ Edit Translations" 按钮中心坐标（physical px）
SLOT_BUTTONS_PHYS: list[tuple[int, int]] = [
    (1584, 825),  # 1
    (1839, 825),  # 2
    (2094, 825),  # 3
    (2349, 825),  # 4
    (2604, 825),  # 5
    (1584, 1227),  # 6
    (1839, 1227),  # 7
    (2094, 1227),  # 8
    (2349, 1227),  # 9
]

# Dialog 内元素（chrome maximized 主屏 3840×2160）—— 完整实测自 it_added.png
# Italian region 添加前 dialog: French + German 两块
# Italian 添加后 dialog 增高 ~230px
ADD_LANGUAGE_DROPDOWN = (1977, 1535)  # Italian 未添加时下拉位置
ADD_MEDIA_BUTTON = (1995, 1395)  # Italian region 内的 Add media 按钮
SAVE_BUTTON = (2517, 1772)  # Italian 添加后 Save 位置
CANCEL_BUTTON = (2425, 1775)  # 同上

# 下拉展开后语言选项（向上展开！）
# Italian 在 dropdown popup 顶部第 3 项
LANGUAGE_OPTION_Y = {
    "english": 830,
    "italian": 860,
    "polish": 890,
    "romanian": 920,
    "dutch": 950,
}
LANGUAGE_OPTION_X = 1900


# ---------- Chrome window helpers ----------


def setup_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
    except Exception:
        pass


def find_chrome_window():
    """找到 EZ chrome 窗口；多种 title fallback（RPA 操作可能 mutate window title）。"""
    import pygetwindow as gw
    wins = list(gw.getAllWindows())
    # 优先匹配标准 title
    for w in wins:
        if w.title and "EZ Product Translate" in w.title and "Shopify" in w.title:
            return w
    # 后备：匹配 admin.shopify.com 的 chrome window
    for w in wins:
        if w.title and "Shopify" in w.title and "Newjoyloo" in w.title:
            return w
    # 再后备：匹配 chrome 中的最大窗口（不是 SunBrowser 等）
    candidates = []
    for w in wins:
        try:
            if not w.title or w.width < 800 or w.height < 600:
                continue
            if "SunBrowser" in w.title or "Visual Studio" in w.title:
                continue
            # chrome window class 名为 "Chrome_WidgetWin_1"
            import win32gui
            cls = win32gui.GetClassName(w._hWnd)
            if "Chrome_WidgetWin_1" in cls:
                candidates.append(w)
        except Exception:
            continue
    if candidates:
        # 取面积最大的
        return max(candidates, key=lambda w: w.width * w.height)
    return None


def ensure_chrome_topmost_max() -> None:
    w = find_chrome_window()
    if not w:
        raise RuntimeError("EZ Product Translate Chrome window not found")
    hwnd = w._hWnd
    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_TOPMOST,
        0, 0, 0, 0,
        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
    )
    # title bar click to ensure foreground / focus
    pyautogui.click(900, 12)
    abortable_sleep(0.4)


# ---------- Clipboard for file path input ----------


def copy_text_to_clipboard(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


# ---------- Atomic actions ----------


def _click(x: int, y: int, *, settle_s: float = 0.4) -> None:
    check_abort()
    pyautogui.moveTo(x, y, duration=0.25)
    abortable_sleep(0.1)
    check_abort()
    pyautogui.click()
    abortable_sleep(settle_s)


def replace_one_slot(slot_idx: int, local_image_path: str, *,
                     language: str = "italian",
                     wait_dialog_open_s: float = 2.5,
                     wait_lang_dropdown_s: float = 1.0,
                     wait_lang_added_s: float = 1.5,
                     wait_file_dialog_s: float = 1.5,
                     wait_save_done_s: float = 3.0) -> None:
    """对某个图位（0-indexed）完整执行替换流程。

    要求：Chrome 已经在 EZ 商品页（maximized），dialog 关闭状态。
    完成后 dialog 关闭，停留在 EZ 商品页。
    """
    if not (0 <= slot_idx < len(SLOT_BUTTONS_PHYS)):
        raise IndexError(f"slot {slot_idx} out of range")
    lang_y = LANGUAGE_OPTION_Y.get(language.lower())
    if lang_y is None:
        raise ValueError(f"unsupported language {language}; add it to LANGUAGE_OPTION_Y")

    ensure_chrome_topmost_max()

    # 1. Edit Translations
    _click(*SLOT_BUTTONS_PHYS[slot_idx], settle_s=wait_dialog_open_s)

    # 2. Add Language dropdown
    _click(*ADD_LANGUAGE_DROPDOWN, settle_s=wait_lang_dropdown_s)

    # 3. Pick target language
    _click(LANGUAGE_OPTION_X, lang_y, settle_s=wait_lang_added_s)

    # 4. Add media → file dialog
    _click(*ADD_MEDIA_BUTTON, settle_s=wait_file_dialog_s)

    # 5. Paste path + Enter
    copy_text_to_clipboard(local_image_path)
    pyautogui.hotkey("ctrl", "v")
    abortable_sleep(0.4)
    pyautogui.press("enter")
    abortable_sleep(2.5)  # wait file load preview

    # 6. Save
    _click(*SAVE_BUTTON, settle_s=wait_save_done_s)


def replace_many(pairs: list[tuple[int, str]], *, language: str = "italian") -> list[dict]:
    """连续替换多个 slot（旧 API，硬编码 9 坐标）。"""
    setup_dpi()
    ensure_chrome_topmost_max()
    results: list[dict] = []
    for idx, path in pairs:
        try:
            replace_one_slot(idx, path, language=language)
            results.append({"slot": idx, "path": path, "status": "ok"})
        except Exception as exc:
            results.append({"slot": idx, "path": path, "status": "failed", "error": str(exc)})
    return results


# ---------- 动态扫描按钮 + 滚动 ----------


_BUTTON_TEMPLATE_PATH = pathlib.Path(__file__).parent / "templates" / "btn_edit_translations.png"


def find_edit_button_centers(*, threshold: float = 0.7,
                              template_path: pathlib.Path = _BUTTON_TEMPLATE_PATH) -> list[tuple[int, int]]:
    """对当前主屏截图做 cv2 模板匹配，返回所有 "+ Edit Translations" 按钮中心物理坐标。

    返回顺序：按 (y_row, x) row-major 排序。EZ app 默认按 Shopify product position 排
    所以返回顺序应与 Shopify product images 顺序一致。
    """
    tpl = cv2.imread(str(template_path))
    if tpl is None:
        raise FileNotFoundError(f"button template not found: {template_path}")
    pil_shot = ImageGrab.grab()
    img = cv2.cvtColor(np.array(pil_shot), cv2.COLOR_RGB2BGR)
    res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    loc = np.where(res >= threshold)
    points = list(zip(*loc[::-1]))
    seen: list[tuple[int, int]] = []
    for x, y in sorted(points):
        if all(abs(x - sx) > 30 or abs(y - sy) > 20 for sx, sy in seen):
            seen.append((x, y))
    h, w = tpl.shape[:2]
    centers = [(x + w // 2, y + h // 2) for x, y in seen]
    centers.sort(key=lambda p: (p[1] // 30, p[0]))
    return centers


def replace_one_at(button_x: int, button_y: int, local_image_path: str, *,
                   language: str = "italian") -> None:
    """在指定按钮位置触发完整替换流程。"""
    lang_y = LANGUAGE_OPTION_Y.get(language.lower())
    if lang_y is None:
        raise ValueError(f"unsupported language {language}")
    ensure_chrome_topmost_max()
    _click(button_x, button_y, settle_s=2.5)  # Edit Translations -> dialog
    _click(*ADD_LANGUAGE_DROPDOWN, settle_s=1.0)
    _click(LANGUAGE_OPTION_X, lang_y, settle_s=1.5)
    _click(*ADD_MEDIA_BUTTON, settle_s=1.5)
    copy_text_to_clipboard(local_image_path)
    pyautogui.hotkey("ctrl", "v")
    abortable_sleep(0.4)
    pyautogui.press("enter")
    abortable_sleep(2.5)
    _click(*SAVE_BUTTON, settle_s=3.0)


def replace_many_dynamic(pairs: list[tuple[int, str]], *,
                          language: str = "italian",
                          max_passes: int = 6,
                          scroll_amount: int = -10) -> list[dict]:
    """动态版：每轮扫一次 viewport 内的按钮，处理后滚动继续。

    pairs = [(shopify_position, local_path), ...] 已按 Shopify position 升序。
    每个 pass 处理当前 viewport 内可见的所有按钮，按视觉 row-major 顺序对应
    pairs 的 [done_count : done_count + visible_count] 段。处理完滚动 viewport，
    再扫描，循环直到 done_count == len(pairs) 或 max_passes 达到。
    """
    setup_dpi()
    ensure_chrome_topmost_max()
    abortable_sleep(0.5)

    results: list[dict] = []
    done = 0
    for pass_idx in range(max_passes):
        if done >= len(pairs):
            break
        ensure_chrome_topmost_max()
        abortable_sleep(0.5)
        centers = find_edit_button_centers()
        print(f"[pass {pass_idx+1}] found {len(centers)} buttons in viewport, done={done}/{len(pairs)}")
        if not centers:
            print("  no buttons in viewport; stopping")
            break
        take = min(len(centers), len(pairs) - done)
        for i in range(take):
            cx, cy = centers[i]
            slot_idx, path = pairs[done + i]
            try:
                replace_one_at(cx, cy, path, language=language)
                results.append({"slot": slot_idx, "path": path, "status": "ok"})
                print(f"  slot {slot_idx} OK -> {path.split(chr(92))[-1]}")
            except AbortSignal as exc:
                results.append({"slot": slot_idx, "path": path, "status": "aborted", "error": str(exc)})
                print(f"  ABORTED at slot {slot_idx}: {exc}")
                return results
            except Exception as exc:
                results.append({"slot": slot_idx, "path": path, "status": "failed", "error": str(exc)})
                print(f"  slot {slot_idx} FAILED: {exc}")
        done += take
        if done >= len(pairs):
            break
        # scroll down for next pass
        print(f"  scrolling viewport (amount={scroll_amount})")
        ensure_chrome_topmost_max()
        try:
            sw, sh = pyautogui.size()
            pyautogui.moveTo(sw // 2, sh // 2, duration=0.2)
            for _ in range(3):
                pyautogui.scroll(scroll_amount)
                abortable_sleep(0.4)
        except Exception:
            pass
        abortable_sleep(1.5)
    return results
