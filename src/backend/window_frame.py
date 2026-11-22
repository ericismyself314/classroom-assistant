"""Let the classroom toolbar share the title area while keeping the Windows resize frame."""
import ctypes
from ctypes import wintypes
import sys


def connect_window_controls(window):
    if sys.platform != "win32":
        return

    user32 = ctypes.windll.user32
    set_dwm_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
    set_dwm_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    set_dwm_attribute.restype = ctypes.c_long
    corner_preference = 33  # DWMWA_WINDOW_CORNER_PREFERENCE
    border_color = 34  # DWMWA_BORDER_COLOR
    default_color = 0xFFFFFFFF
    gdi32 = ctypes.windll.gdi32
    gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
    gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
    gdi32.CreateRectRgn.restype = wintypes.HRGN
    gdi32.CombineRgn.argtypes = [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    window_dpi = getattr(user32, "GetDpiForWindow", None)
    style_index = -16  # GWL_STYLE
    caption = 0x00C00000  # WS_CAPTION; leave the system menu and sizing frame intact.
    frame_changed = 0x37  # FRAMECHANGED | NOMOVE | NOSIZE | NOZORDER | NOACTIVATE
    compact = False
    system_corners = False
    subclassed = False
    corner_size = None
    native_drag_regions = False
    common = ctypes.windll.comctl32
    word = ctypes.c_size_t
    subclass_type = ctypes.WINFUNCTYPE(wintypes.LPARAM, wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM, word, word)
    common.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    common.DefSubclassProc.restype = wintypes.LPARAM
    common.SetWindowSubclass.argtypes = [wintypes.HWND, subclass_type, word, word]
    common.RemoveWindowSubclass.argtypes = [wintypes.HWND, subclass_type, word]

    def handle():
        return wintypes.HWND(window.native.Handle.ToInt64())

    def prepare_drag_regions():
        control = window.native.webview

        def enable(sender, _event):
            nonlocal native_drag_regions
            core = sender.CoreWebView2
            if core is not None:
                try:
                    core.Settings.IsNonClientRegionSupportEnabled = True
                    native_drag_regions = core.Settings.IsNonClientRegionSupportEnabled
                except Exception:
                    # An older runtime keeps its operating-system title bar and controls.
                    native_drag_regions = False

        if hasattr(control, "CoreWebView2InitializationCompleted"):
            if control.CoreWebView2 is None:
                control.CoreWebView2InitializationCompleted += enable
            else:
                enable(control, None)

    def set_frame_attribute(hwnd, attribute, value):
        data = wintypes.DWORD(value)
        return set_dwm_attribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)) == 0

    def match_frame_border(hwnd):
        from System.Windows.Forms import SystemInformation

        # A suppressed border exposes the dark backdrop in the one-pixel sizing frame.
        # Match the app's #f5f5f7 surface while allowing Windows to own high-contrast colors.
        color = default_color if SystemInformation.HighContrast else 0x00F7F5F5
        set_frame_attribute(hwnd, border_color, color)

    def update_legacy_corners(hwnd, rounded):
        nonlocal corner_size
        region = None
        size = None
        if rounded:
            bounds = wintypes.RECT()
            client = wintypes.RECT()
            origin = wintypes.POINT()
            if not (user32.GetWindowRect(hwnd, ctypes.byref(bounds))
                    and user32.GetClientRect(hwnd, ctypes.byref(client))
                    and user32.ClientToScreen(hwnd, ctypes.byref(origin))):
                return
            width, height = bounds.right - bounds.left, bounds.bottom - bounds.top
            dpi = (window_dpi(hwnd) if window_dpi else 96) or 96
            x, y = origin.x - bounds.left, origin.y - bounds.top
            # The sizing frame includes invisible gutters. Round the visible frame,
            # then keep the straight resize edges available outside that outline.
            size = (width, height, max(0, x - 1), max(0, y - 1),
                    min(width, x + client.right + 1), min(height, y + client.bottom + 1),
                    round(22 * dpi / 96))
        if size == corner_size:
            return
        if size:
            width, height, left, top, right, bottom, radius = size
            region = gdi32.CreateRoundRectRgn(left, top, right + 1, bottom + 1, 2 * radius, 2 * radius)
            if not region:
                return
            for rect in ((0, top + radius, width, bottom - radius),
                         (left + radius, 0, right - radius, height)):
                edge = gdi32.CreateRectRgn(*rect)
                ok = edge and gdi32.CombineRgn(region, region, edge, 2)  # RGN_OR
                if edge:
                    gdi32.DeleteObject(edge)
                if not ok:
                    gdi32.DeleteObject(region)
                    return
        previous, corner_size = corner_size, size  # SetWindowRgn can synchronously send size messages.
        if not user32.SetWindowRgn(hwnd, region, True):
            corner_size = previous
            if region:
                gdi32.DeleteObject(region)  # Windows takes ownership only on success.

    @subclass_type
    def frame_proc(hwnd, message, wparam, lparam, subclass_id, _data):
        if compact and not system_corners and message in (0x0005, 0x02E0):  # WM_SIZE or WM_DPICHANGED
            result = common.DefSubclassProc(hwnd, message, wparam, lparam)
            native = wintypes.HWND(hwnd)
            if not user32.IsIconic(native):
                update_legacy_corners(native, not user32.IsZoomed(native))
            return result
        if compact and system_corners and message == 0x001A:  # WM_SETTINGCHANGE includes high contrast.
            result = common.DefSubclassProc(hwnd, message, wparam, lparam)
            match_frame_border(hwnd)
            return result
        if message == 0x0083 and not (user32.GetWindowLongW(wintypes.HWND(hwnd), style_index) & caption):  # WM_NCCALCSIZE
            # The standard sizing frame otherwise paints a dark strip across the top.
            # On a maximized window its inset already sits outside the monitor work area.
            if not user32.IsZoomed(wintypes.HWND(hwnd)) and not user32.IsIconic(wintypes.HWND(hwnd)):
                rect = wintypes.RECT.from_address(lparam)
                top = rect.top
                common.DefSubclassProc(hwnd, message, wparam, lparam)
                rect.top = top
                return 0
        elif message == 0x0082:  # WM_NCDESTROY
            common.RemoveWindowSubclass(hwnd, frame_proc, subclass_id)
        return common.DefSubclassProc(hwnd, message, wparam, lparam)

    def install_frame():
        nonlocal subclassed
        if not subclassed:
            # SetWindowSubclass must run on the thread that owns the WinForms window.
            from System import Action

            def attach():
                nonlocal subclassed
                subclassed = bool(common.SetWindowSubclass(handle(), frame_proc, 1, 0))

            window.native.Invoke(Action(attach))
        return subclassed

    def change_caption(show):
        hwnd = handle()
        style = user32.GetWindowLongW(hwnd, style_index)
        if not style:
            return False
        updated = style | caption if show else style & ~caption
        if updated != style:
            user32.SetWindowLongW(hwnd, style_index, updated)
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, frame_changed)
        return (user32.GetWindowLongW(hwnd, style_index) & caption) == (caption if show else 0)

    def restore_native_caption():
        nonlocal compact, system_corners
        if compact:
            compact = False
            hwnd = handle()
            if system_corners:
                set_frame_attribute(hwnd, border_color, default_color)
                set_frame_attribute(hwnd, corner_preference, 0)  # DWMWCP_DEFAULT
                system_corners = False
            else:
                update_legacy_corners(hwnd, False)
            change_caption(True)

    def desktop_frame(action):
        nonlocal compact, system_corners
        hwnd = handle()
        if action == "ready":
            # Keep the normal caption until the page has installed usable controls.
            if not compact and native_drag_regions and install_frame() and change_caption(False):
                # The compositor can smooth the entire outline only when there is no GDI region.
                system_corners = set_frame_attribute(hwnd, corner_preference, 2)  # DWMWCP_ROUND
                compact = True
                if system_corners:
                    match_frame_border(hwnd)
                elif not user32.IsIconic(hwnd):
                    update_legacy_corners(hwnd, not user32.IsZoomed(hwnd))
        elif not compact:
            return None
        elif action == "resize-top":
            point = wintypes.POINT()
            if user32.GetCursorPos(ctypes.byref(point)):
                position = (point.x & 0xFFFF) | ((point.y & 0xFFFF) << 16)
                user32.PostMessageW(hwnd, 0x00A1, 12, position)  # WM_NCLBUTTONDOWN, HTTOP
        elif action == "size":
            if user32.IsZoomed(hwnd):
                window.restore()
            else:
                window.maximize()
        elif action == "minimize":
            window.minimize()
        elif action == "close":
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE uses the normal shutdown path.
        elif action != "state":
            return None
        return {"maximized": bool(user32.IsZoomed(hwnd))} if compact else None

    # WebView2 reads this setting on navigation; the document's ready callback is too late.
    window.events.before_show += prepare_drag_regions
    window.events.before_load += restore_native_caption
    window.expose(desktop_frame)
