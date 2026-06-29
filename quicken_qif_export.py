#!/usr/bin/env python3
"""
Quicken QIF Auto-Export

Automates Quicken's File > Export > QIF workflow for all accounts.
Connects to a running Quicken instance, discovers accounts from the
export dialog dropdown, and exports each account's QIF file.

Requires Quicken to be open and logged in on the Windows desktop.

Usage:
    python quicken_qif_export.py [--dry-run]
    python quicken_qif_export.py --accounts "Cash - Ram,Cash - Lori"
    python quicken_qif_export.py --no-skip-existing

Dependencies:
    pip install pywinauto
"""

import argparse
import ast
import ctypes
import ctypes.wintypes as wintypes
import logging
import os
import re
import sys
import time
from pathlib import Path

try:
    import pywinauto
    from pywinauto import Application
    from pywinauto.findwindows import ElementNotFoundError
    from pywinauto.timings import wait_until_passes, TimeoutError as WaitTimeoutError
except ImportError:
    print("ERROR: pywinauto is required. Install with: pip install pywinauto")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Win32 helper — properly typed SendMessageW for 64-bit Python
# ---------------------------------------------------------------------------

def _setup_win32():
    """Declare proper argtypes for Win32 functions used throughout."""
    user32 = ctypes.windll.user32
    # On 64-bit Windows: HWND, WPARAM, LPARAM are all pointer-width (8 bytes).
    # Without this, ctypes truncates 64-bit handles to 32-bit c_int.
    LPDWORD = ctypes.POINTER(ctypes.c_ulong)
    user32.SendMessageW.argtypes = [
        ctypes.c_void_p,  # HWND
        ctypes.c_uint,    # UINT msg
        ctypes.c_void_p,  # WPARAM (pointer-width)
        ctypes.c_void_p,  # LPARAM (pointer-width)
    ]
    user32.SendMessageW.restype = ctypes.c_void_p  # LRESULT
    user32.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p, LPDWORD
    ]
    user32.EnumWindows.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p
    ]
    user32.FindWindowExW.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p
    ]
    user32.FindWindowExW.restype = ctypes.c_void_p
    return user32


user32 = _setup_win32()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config(config_file="quicken_qif_export.config"):
    """
    Load configuration from an INI-style config file.

    Format is simple key = value pairs (same style as qif_sanitizer.config).
    Lines starting with '#' are comments.  Values that look like Python lists
    are parsed with ast.literal_eval.  Quoted strings are unquoted.
    """
    config = {}
    config_path = Path(config_file)
    if not config_path.exists():
        print(f"Warning: config file '{config_file}' not found, using defaults")
        return config

    with open(config_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            # Strip quotes
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            config[key] = value

    return config


def get_config_value(config, key, cli_value, default=None):
    """Return cli_value if set, else config value, else default."""
    if cli_value is not None:
        return cli_value
    return config.get(key, default)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_file=None):
    """Configure logging to console and optionally to a file."""
    fmt = "%(asctime)s  %(levelname)-8s  %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


# ---------------------------------------------------------------------------
# Quicken QIF Exporter
# ---------------------------------------------------------------------------

class QuickenQIFExporter:
    """
    Connects to a running Quicken for Windows instance and automates
    the File > Export > QIF workflow for each account.
    """

    def __init__(self, config):
        self.delay = float(config.get("DELAY_BETWEEN_ACCOUNTS", "2"))
        self.timeout = int(config.get("TIMEOUT", "60"))
        self.window_title = config.get("QUICKEN_WINDOW_TITLE", "Quicken")
        self.output_dir = Path(config.get("OUTPUT_DIR", "quicken_export_files"))
        self.app = None
        self.main_window = None
        self.quicken_pid = None
        # Configurable UI timing delays
        self.delay_keystroke = float(config.get("DELAY_KEYSTROKE", "0.3"))
        self.delay_menu = float(config.get("DELAY_MENU", "0.5"))
        self.delay_combo_enum = float(config.get("DELAY_COMBO_ENUM", "0.01"))
        self.delay_dialog_action = float(config.get("DELAY_DIALOG_ACTION", "0.5"))
        self.delay_idle_poll = float(config.get("DELAY_IDLE_POLL", "2.0"))

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self):
        """
        Attach to the running Quicken process.

        Tries the UIA backend first, then falls back to win32.
        """
        title_re = f".*{re.escape(self.window_title)}.*"
        for backend in ("uia", "win32"):
            try:
                self.app = Application(backend=backend).connect(
                    title_re=title_re, timeout=self.timeout
                )
                self.main_window = self.app.window(title_re=title_re)
                self.main_window.wait("ready", timeout=self.timeout)
                logging.info(
                    "Connected to Quicken (backend=%s, title='%s')",
                    backend, self.main_window.window_text(),
                )
                return True
            except Exception as exc:
                logging.debug("Backend %s failed: %s", backend, exc)
        logging.error(
            "Could not connect to Quicken.  Make sure it is open and logged in."
        )
        return False

    def _bring_to_front(self):
        """Ensure the Quicken window is in the foreground."""
        try:
            self.main_window.set_focus()
            time.sleep(self.delay_keystroke)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Export dialog helpers
    # ------------------------------------------------------------------

    def open_export_dialog(self):
        """
        Open the File > Export > QIF dialog via keyboard navigation.

        Returns the dialog window specification or None on failure.
        """
        self._bring_to_front()

        # Alt+F opens the File menu
        self.main_window.type_keys("%f", pause=0.05)
        time.sleep(self.delay_menu)

        # Look for the Export submenu — try keyboard shortcut 'E' first,
        # then fall back to sending arrow keys.
        try:
            self.main_window.type_keys("e", pause=0.05)
            time.sleep(self.delay_keystroke)
        except Exception:
            pass

        # Look for QIF File menu item — try 'Q' shortcut
        try:
            self.main_window.type_keys("q", pause=0.05)
            time.sleep(self.delay_keystroke)
        except Exception:
            pass

        # The export dialog should now be opening.  Wait for it.
        return self._wait_for_export_dialog()

    def _wait_for_export_dialog(self):
        """
        Wait for the QIF Export dialog to appear and become ready.

        Uses Win32 API (EnumWindows) directly to find new windows belonging
        to the Quicken process.  This is orders of magnitude faster than
        pywinauto's UIA tree traversal which chokes on Quicken's massive UI.

        Returns the dialog window or None on timeout.
        """
        user32 = ctypes.windll.user32
        main_hwnd = self.main_window.handle

        # Get Quicken's process ID from the main window
        lpdw_pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(main_hwnd, ctypes.byref(lpdw_pid))
        quicken_pid = lpdw_pid.value
        self.quicken_pid = quicken_pid

        logging.debug("Quicken PID: %d, main HWND: %d", quicken_pid, main_hwnd)

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            found_hwnds = []

            # Use Win32 EnumWindows — very fast, no UIA tree traversal
            WNDENUMPROC = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
            )

            def _enum_callback(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                # Get the process ID for this window
                pid = ctypes.c_ulong(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value != quicken_pid:
                    return True
                # Skip the main window
                if hwnd == main_hwnd:
                    return True
                found_hwnds.append(hwnd)
                return True

            callback = WNDENUMPROC(_enum_callback)
            user32.EnumWindows(callback, 0)

            for hwnd in found_hwnds:
                # Get window title
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    continue
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                logging.info("Found new Quicken window: HWND=%d title='%s'", hwnd, title)

                # Only accept windows that are actually the QIF Export dialog.
                # Reject stale overwrite-confirmation popups etc.
                if "qif" not in title.lower() and "export" not in title.lower():
                    logging.info("  Skipping non-export window")
                    continue

                # Wrap the HWND in a pywinauto WindowSpecification
                try:
                    dlg = self.app.window(handle=hwnd)
                    dlg.wait("ready", timeout=min(10, deadline - time.time()))
                    return dlg
                except Exception as exc:
                    logging.debug("Failed to wrap HWND %d: %s", hwnd, exc)

            time.sleep(self.delay_dialog_action)

        logging.error("Timeout waiting for QIF Export dialog to appear")
        return None

    def close_dialog(self, dialog):
        """Close a dialog by pressing Escape or clicking Cancel."""
        if dialog is None:
            return
        try:
            dialog.type_keys("{ESCAPE}", pause=0.05)
            time.sleep(self.delay_keystroke)
        except Exception:
            try:
                cancel = dialog.child_window(title="Cancel", control_type="Button")
                if cancel.exists(timeout=1):
                    cancel.click_input()
                    time.sleep(self.delay_keystroke)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Account discovery
    # ------------------------------------------------------------------

    def discover_accounts(self):
        """
        Open the export dialog, read all account names from the dropdown,
        then close the dialog.

        Returns a sorted list of account name strings.
        """
        logging.info("Discovering accounts from QIF Export dialog...")
        dialog = self.open_export_dialog()
        if dialog is None:
            return []

        accounts = self._read_account_dropdown(dialog)
        self.close_dialog(dialog)
        time.sleep(self.delay)

        # Verify the dialog actually closed
        try:
            if dialog.exists(timeout=3):
                logging.warning("Dialog still open after Escape — pressing Escape again")
                dialog.type_keys("{ESCAPE}", pause=0.05)
                time.sleep(self.delay_keystroke)
                if dialog.exists(timeout=3):
                    logging.warning("Dialog still open — trying Cancel button")
                    self.close_dialog(dialog)
                    time.sleep(self.delay_keystroke)
        except Exception:
            pass

        if not accounts:
            logging.warning(
                "No accounts found in the export dialog dropdown.  "
                "You may need to adjust the control identification."
            )
        else:
            logging.info("Discovered %d accounts", len(accounts))

        return sorted(accounts)

    def _read_account_dropdown(self, dialog):
        """
        Read all account names from the export dialog's account dropdown.

        Uses Win32 API to read combo box items directly, avoiding pywinauto's
        slow UIA tree traversal which chokes on Quicken's massive UI.

        Quicken keyboard shortcuts in the export dialog:
            ALT+q = QIF file to export to
            ALT+a = Quicken account to export from
            ALT+i = Include transactions in dates from
            ALT+o = Include transactions in dates to
        """
        user32 = ctypes.windll.user32

        # Press ALT+a to move focus to the account dropdown
        logging.info("Pressing ALT+a to navigate to account dropdown")
        try:
            dialog.set_focus()
            time.sleep(self.delay_keystroke)
            dialog.type_keys("%a", pause=0.05)
            time.sleep(self.delay_menu)
        except Exception as exc:
            logging.debug("ALT+a failed: %s", exc)

        # Use Win32 GetFocus to find the focused control
        focused_hwnd = user32.GetFocus()
        if not focused_hwnd:
            # Fallback: get focus from dialog's HWND
            dlg_hwnd = dialog.handle
            user32.SetFocus(dlg_hwnd)
            time.sleep(self.delay_keystroke)
            # Re-send ALT+a
            try:
                dialog.type_keys("%a", pause=0.05)
                time.sleep(self.delay_menu)
            except Exception:
                pass
            focused_hwnd = user32.GetFocus()

        if focused_hwnd:
            logging.info("Focused control HWND: %d", focused_hwnd)
            # Try reading as a ComboBox using Win32 messages
            accounts = self._read_combo_via_win32(focused_hwnd)
            if accounts:
                return accounts

        # Fallback: use Win32 to find ComboBox child windows of the dialog
        logging.info("Trying to find ComboBox child windows via Win32...")
        accounts = self._find_combo_in_dialog(dialog.handle)
        if accounts:
            return accounts

        # Last resort: dump child windows for debugging
        logging.warning("Could not locate account dropdown.  Dumping dialog children:")
        self._dump_controls_win32(dialog.handle)

        return []

    def _read_combo_via_win32(self, hwnd):
        """
        Read items from a ComboBox control using Win32 CB_GETCOUNT and
        CB_GETLBTEXT messages.  Very fast — no UIA tree traversal.
        """
        CB_GETCOUNT = 0x0146
        CB_GETLBTEXT = 0x0148
        CB_GETLBTEXTLEN = 0x0149
        CB_SETCURSEL = 0x014E
        CB_GETCURSEL = 0x0147

        items = []
        try:
            count = user32.SendMessageW(hwnd, CB_GETCOUNT, 0, 0)
            logging.info("ComboBox item count: %d", count)
            if count <= 0 or count > 500:
                return []

            for i in range(count):
                # Select the item to ensure the text is valid
                user32.SendMessageW(hwnd, CB_SETCURSEL, i, 0)
                time.sleep(self.delay_combo_enum)

                text_len = user32.SendMessageW(hwnd, CB_GETLBTEXTLEN, i, 0)
                if text_len <= 0:
                    items.append("")
                    continue

                buf = ctypes.create_unicode_buffer(text_len + 1)
                # CB_GETLBTEXT expects a pointer to a buffer.
                # Use c_void_p to avoid 64-bit overflow in LPARAM.
                result = user32.SendMessageW(hwnd, CB_GETLBTEXT, i,
                                             ctypes.c_void_p(ctypes.addressof(buf)))
                if result >= 0:
                    items.append(buf.value)
                else:
                    items.append("")

            if items:
                logging.info("Read %d items from ComboBox via Win32", len(items))
                for i, item in enumerate(items):
                    logging.debug("  [%d] %s", i, item)

            # Restore selection to first item
            if items:
                user32.SendMessageW(hwnd, CB_SETCURSEL, 0, 0)

        except Exception as exc:
            logging.warning("Win32 combo read failed for HWND %d: %s", hwnd, exc)

        return items

    def _find_combo_in_dialog(self, dlg_hwnd):
        """
        Find ComboBox child windows in the dialog using Win32 FindWindowEx.
        Read their items using Win32 messages.
        """
        user32 = ctypes.windll.user32
        CB_GETCOUNT = 0x0146
        CB_GETLBTEXT = 0x0148
        CB_GETLBTEXTLEN = 0x0149
        CB_SETCURSEL = 0x014E
        WM_GETTEXT = 0x000D
        COMBOBOX_CLASS = "ComboBox"

        best_items = []

        # Enumerate child windows looking for ComboBox controls
        child_hwnd = user32.GetWindow(dlg_hwnd, 5)  # GW_CHILD = 5
        while child_hwnd:
            # Check if this is a ComboBox
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child_hwnd, cls_buf, 256)
            cls_name = cls_buf.value

            if "combo" in cls_name.lower():
                logging.info("Found ComboBox child: HWND=%d class='%s'", child_hwnd, cls_name)
                items = self._read_combo_via_win32(child_hwnd)
                if len(items) > len(best_items):
                    best_items = items

            # Also check grandchildren (ComboBox → ListBox)
            grandchild = user32.GetWindow(child_hwnd, 5)  # GW_CHILD
            while grandchild:
                gc_cls_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(grandchild, gc_cls_buf, 256)
                gc_cls = gc_cls_buf.value
                if "combo" in gc_cls.lower():
                    items = self._read_combo_via_win32(grandchild)
                    if len(items) > len(best_items):
                        best_items = items
                grandchild = user32.GetWindow(grandchild, 2)  # GW_HWNDNEXT = 2

            child_hwnd = user32.GetWindow(child_hwnd, 2)  # GW_HWNDNEXT = 2

        return best_items

    def _dump_controls_win32(self, dlg_hwnd):
        """Log all child window handles, class names, and titles for debugging."""
        user32 = ctypes.windll.user32
        WM_GETTEXT = 0x000D
        child_hwnd = user32.GetWindow(dlg_hwnd, 5)  # GW_CHILD
        depth = 0
        while child_hwnd:
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child_hwnd, cls_buf, 256)

            title_buf = ctypes.create_unicode_buffer(512)
            user32.SendMessageW(child_hwnd, WM_GETTEXT, 512,
                               ctypes.c_void_p(ctypes.addressof(title_buf)))

            visible = user32.IsWindowVisible(child_hwnd)
            logging.info("  Child HWND=%d class='%s' title='%s' visible=%s",
                         child_hwnd, cls_buf.value, title_buf.value, visible)

            # Recurse one level into children
            grandchild = user32.GetWindow(child_hwnd, 5)
            while grandchild:
                gc_cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(grandchild, gc_cls, 256)
                gc_title = ctypes.create_unicode_buffer(512)
                user32.SendMessageW(grandchild, WM_GETTEXT, 512,
                                   ctypes.c_void_p(ctypes.addressof(gc_title)))
                logging.info("    Grandchild HWND=%d class='%s' title='%s'",
                             grandchild, gc_cls.value, gc_title.value)
                grandchild = user32.GetWindow(grandchild, 2)

            child_hwnd = user32.GetWindow(child_hwnd, 2)  # GW_HWNDNEXT

    # Windows-illegal chars in filenames
    _ILLEGAL_FILENAME_RE = re.compile(r'[\\/:*?"<>|]')

    @classmethod
    def _sanitize_filename(cls, name):
        """Replace filesystem-illegal characters with underscores."""
        return cls._ILLEGAL_FILENAME_RE.sub("_", name)

    # ------------------------------------------------------------------
    # Export a single account
    # ------------------------------------------------------------------

    def export_account(self, account_name):
        """
        Export a single account's QIF file.

        Steps:
        1. Open the File > Export > QIF dialog
        2. Select the account from the dropdown
        3. Set the output file path
        4. Click OK
        5. Wait for the export to finish

        Returns True on success, False on failure.
        """
        safe_name = self._sanitize_filename(account_name)
        output_file = self.output_dir / f"{safe_name}.qif"
        output_path_str = str(output_file.resolve())

        logging.info("Exporting account: %s", account_name)
        dialog = self.open_export_dialog()
        if dialog is None:
            return False

        # Select the account in the dropdown
        if not self._select_account_in_dialog(dialog, account_name):
            logging.error("Failed to select account '%s' in export dialog", account_name)
            self.close_dialog(dialog)
            return False

        time.sleep(self.delay_keystroke)

        # Set the file path
        if not self._set_file_path(dialog, output_path_str):
            logging.error("Failed to set file path: %s", output_path_str)
            self.close_dialog(dialog)
            return False

        time.sleep(self.delay_keystroke)

        # Click OK to start the export
        if not self._click_ok(dialog):
            logging.error("Failed to click OK in export dialog")
            self.close_dialog(dialog)
            return False

        # Wait for export to complete
        success = self._wait_for_export_complete(dialog, output_file)

        # Always close the dialog to clean up before the next account
        self.close_dialog(dialog)

        if success:
            logging.info("Exported: %s -> %s", account_name, output_file.name)
        else:
            logging.warning("Export may have failed for: %s", account_name)

        time.sleep(self.delay)
        return success

    def _select_account_in_dialog(self, dialog, account_name):
        """
        Select an account in the export dialog's combo box.

        Uses ALT+a to navigate to the dropdown, then either:
        1. Selects by Win32 CB_FINDSTRING + CB_SETCURSEL (fast, no UIA)
        2. Falls back to typing the name into the focused control
        """
        user32 = ctypes.windll.user32
        CB_FINDSTRING = 0x014C
        CB_SETCURSEL = 0x014E
        CB_GETCOUNT = 0x0146

        # Navigate to the account dropdown with ALT+a
        try:
            dialog.set_focus()
            time.sleep(self.delay_keystroke)
            dialog.type_keys("%a", pause=0.05)
            time.sleep(self.delay_menu)
        except Exception as exc:
            logging.debug("ALT+a to account dropdown failed: %s", exc)

        # Get the focused control handle
        focused_hwnd = user32.GetFocus()
        if not focused_hwnd:
            logging.error("No focused control after ALT+a")
            return False

        logging.info("Account dropdown HWND: %d", focused_hwnd)

        # Check if it's a ComboBox by class name
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(focused_hwnd, cls_buf, 256)
        cls_name = cls_buf.value
        logging.info("Focused control class: '%s'", cls_name)

        if "combo" in cls_name.lower():
            # Try Win32 CB_FINDSTRING to select the account
            count = user32.SendMessageW(focused_hwnd, CB_GETCOUNT, 0, 0)
            logging.info("ComboBox has %d items", count)

            if count > 0:
                # CB_FINDSTRING: search for exact match first (wParam = start index, lParam = string)
                # Use -1 to search from beginning, with exact match
                search_buf = ctypes.create_unicode_buffer(account_name)
                result = user32.SendMessageW(focused_hwnd, CB_FINDSTRING, -1,
                                             ctypes.c_void_p(ctypes.addressof(search_buf)))
                if result != 0xFFFFFFFF:  # CB_ERR
                    user32.SendMessageW(focused_hwnd, CB_SETCURSEL, result, 0)
                    time.sleep(self.delay_keystroke)
                    logging.info("Selected account '%s' at index %d", account_name, result)
                    return True

                # Try partial match
                for suffix_len in range(len(account_name), 0, -1):
                    partial = account_name[:suffix_len]
                    search_buf = ctypes.create_unicode_buffer(partial)
                    result = user32.SendMessageW(focused_hwnd, CB_FINDSTRING, -1,
                                                 ctypes.c_void_p(ctypes.addressof(search_buf)))
                    if result != 0xFFFFFFFF:
                        user32.SendMessageW(focused_hwnd, CB_SETCURSEL, result, 0)
                        time.sleep(self.delay_keystroke)
                        logging.info("Selected account '%s' (partial match at index %d)",
                                     partial, result)
                        return True

        # Fallback: type the account name into the focused control
        logging.info("Falling back to typing account name")
        try:
            dialog.type_keys("^a", pause=0.05)
            time.sleep(self.delay_keystroke)
            dialog.type_keys(account_name, with_spaces=True, pause=0.02)
            time.sleep(self.delay_keystroke)
            dialog.type_keys("{ENTER}", pause=0.05)
            time.sleep(self.delay_keystroke)
            return True
        except Exception as exc:
            logging.debug("Typing account name failed: %s", exc)

        return False

    def _set_combo_value(self, combo, value):
        """Set a combo box value using Win32 CB_FINDSTRING + CB_SETCURSEL."""
        # This method is kept for compatibility but _select_account_in_dialog
        # now handles combo selection directly via Win32.
        CB_FINDSTRING = 0x014C
        CB_SETCURSEL = 0x014E

        try:
            hwnd = combo.handle
            search_buf = ctypes.create_unicode_buffer(value)
            result = ctypes.windll.user32.SendMessageW(
                hwnd, CB_FINDSTRING, -1, ctypes.c_void_p(ctypes.addressof(search_buf))
            )
            if result != 0xFFFFFFFF:
                ctypes.windll.user32.SendMessageW(hwnd, CB_SETCURSEL, result, 0)
                time.sleep(self.delay_keystroke)
                return True
        except Exception:
            pass

        # Fallback: type it
        try:
            combo.set_focus()
            time.sleep(self.delay_keystroke)
            combo.type_keys("^a{DELETE}", pause=0.05)
            combo.type_keys(value, with_spaces=True, pause=0.02)
            combo.type_keys("{ENTER}", pause=0.05)
            time.sleep(self.delay_keystroke)
            return True
        except Exception:
            pass

        return False

    @staticmethod
    def _escape_type_keys(text):
        """Escape special characters for pywinauto type_keys."""
        for ch in "()+":
            text = text.replace(ch, "{" + ch + "}")
        return text

    def _set_file_path(self, dialog, file_path):
        """
        Set the output file path in the export dialog's text field.

        Uses ALT+q to navigate to the "QIF file to export to" field,
        then types the full file path.  No UIA traversal — just keyboard.
        """
        try:
            dialog.set_focus()
            time.sleep(self.delay_keystroke)

            # Navigate to the file path field with ALT+q
            dialog.type_keys("%q", pause=0.05)
            time.sleep(self.delay_menu)

            # Select all existing text and replace with our path
            dialog.type_keys("^a", pause=0.05)
            time.sleep(self.delay_keystroke)
            dialog.type_keys(self._escape_type_keys(file_path), with_spaces=True, pause=0.01)
            time.sleep(self.delay_keystroke)

            logging.info("File path set to: %s", file_path)
            return True
        except Exception as exc:
            logging.error("Failed to set file path: %s", exc)
            return False

    def _type_into_field(self, control, text):
        """Type text into a control, selecting all existing text first."""
        try:
            control.wait("ready", timeout=3)
            control.set_focus()
            time.sleep(self.delay_keystroke)
            control.type_keys("^a", pause=0.05)
            time.sleep(self.delay_keystroke)
            control.type_keys(text, with_spaces=True, pause=0.01)
            time.sleep(self.delay_keystroke)
            return True
        except Exception as exc:
            logging.debug("Error typing into field: %s", exc)
            return False

    def _click_ok(self, dialog):
        """Click the OK button in the export dialog."""
        # Capture baseline HWNDs BEFORE clicking OK so we can detect
        # any new overwrite-confirmation popup afterwards.
        self._export_baseline_hwnds = self._get_quicken_top_level_hwnds()

        # Strategy 1: find by title
        for title in ("OK", "&OK", "Export", "&Export"):
            try:
                btn = dialog.child_window(title=title, control_type="Button")
                if btn.exists(timeout=2) and btn.is_enabled():
                    btn.click_input()
                    time.sleep(self.delay_dialog_action)
                    if self._handle_overwrite_dialog(self._export_baseline_hwnds):
                        return True
                    logging.error("Export failed — overwrite dialog appeared but was not dismissed")
                    return False
            except Exception:
                continue

        # Strategy 2: find by automation ID
        try:
            btn = dialog.child_window(auto_id="1", control_type="Button")
            if btn.exists(timeout=2) and btn.is_enabled():
                btn.click_input()
                time.sleep(self.delay_dialog_action)
                if self._handle_overwrite_dialog(self._export_baseline_hwnds):
                    return True
                logging.error("Export failed — overwrite dialog appeared but was not dismissed")
                return False
        except Exception:
            pass

        # Strategy 3: press Enter (OK is usually the default button)
        try:
            dialog.type_keys("{ENTER}", pause=0.05)
            time.sleep(self.delay_dialog_action)
            if self._handle_overwrite_dialog(self._export_baseline_hwnds):
                return True
            logging.error("Overwrite dialog was NOT dismissed")
            return False
        except Exception:
            pass

        return False

    def _handle_overwrite_dialog(self, known_hwnds):
        """
        Check if Quicken popped up a confirmation dialog after clicking OK
        (e.g., 'File already exists — overwrite?') and dismiss it.

        known_hwnds = set of HWNDs captured BEFORE clicking OK.
        Any new top-level window in Quicken's process is a confirmation popup.
        We click the Yes button or fallback to Enter.

        Returns True if no dialog appeared (export proceeded normally) or
        if the dialog was found AND dismissed. Returns False only if a
        dialog appeared but could not be dismissed.
        """
        for attempt in range(10):
            time.sleep(self.delay_dialog_action)

            current_hwnds = self._get_quicken_top_level_hwnds()
            new_hwnds = current_hwnds - known_hwnds

            for hwnd in new_hwnds:
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                title = buf.value
                logging.info("New Quicken dialog detected (HWND=%d): '%s'", hwnd, title)

                # Try clicking a Yes/OK button via Win32
                self._click_dialog_button(hwnd, ("yes", "&yes"))
                time.sleep(self.delay_dialog_action)

                # Verify the dialog is actually gone
                if self._verify_dialog_dismissed(hwnd):
                    logging.info("Overwrite dialog dismissed successfully")
                    return True

                # Dialog still visible — try Enter key directly
                logging.info("Dialog still visible, trying Enter key via SendMessage")
                VK_RETURN = 0x0D
                user32.SendMessageW(hwnd, 0x0100, ctypes.c_void_p(VK_RETURN), ctypes.c_void_p(0))  # WM_KEYDOWN
                user32.SendMessageW(hwnd, 0x0101, ctypes.c_void_p(VK_RETURN), ctypes.c_void_p(0))  # WM_KEYUP
                time.sleep(self.delay_dialog_action)

                if self._verify_dialog_dismissed(hwnd):
                    logging.info("Overwrite dialog dismissed via Enter")
                    return True

                # Still visible — try clicking any QC_button
                logging.info("Dialog still visible, trying to click any button")
                self._click_any_button(hwnd)
                time.sleep(self.delay_dialog_action)

                if self._verify_dialog_dismissed(hwnd):
                    logging.info("Overwrite dialog dismissed via any button")
                    return True

                logging.warning("Overwrite dialog (HWND=%d) still visible after all attempts", hwnd)

        logging.info("No overwrite dialog needed — export proceeding")
        return True

    def _verify_dialog_dismissed(self, hwnd, timeout=2.0):
        """Check that a dialog window is no longer visible."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not user32.IsWindowVisible(hwnd):
                return True
            time.sleep(self.delay_dialog_action)
        return False

    def _click_any_button(self, dlg_hwnd):
        """Find and BM_CLICK any button-like control in the dialog."""
        BM_CLICK = 0x00F5
        child = user32.GetWindow(dlg_hwnd, 5)  # GW_CHILD
        while child:
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child, cls_buf, 256)
            cls = cls_buf.value.lower()
            if "button" in cls or "qc_button" in cls:
                txt_buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(child, txt_buf, 256)
                logging.info("  BM_CLICK on '%s' (HWND=%d)", txt_buf.value, child)
                user32.SendMessageW(child, BM_CLICK, 0, 0)
                return True
            child = user32.GetWindow(child, 2)

    def _get_quicken_top_level_hwnds(self):
        """Return a set of all visible top-level HWNDs belonging to Quicken."""
        hwnds = set()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != self.quicken_pid:
                return True
            hwnds.add(hwnd)
            return True

        user32.EnumWindows(_cb, 0)
        return hwnds

    def _click_dialog_button(self, dlg_hwnd, titles):
        """
        Click a button by title in a dialog found by HWND.
        Searches up to 3 levels deep and also checks Quicken's custom
        'QC_button' controls, not just standard Windows 'Button' class.

        Uses SendMessage (synchronous) so Quicken is forced to process the
        click before we continue.  PostMessage is async and Quicken may
        not process it before we move on.
        """
        def _is_button(cls):
            cl = cls.lower()
            return "button" in cl or "qc_button" in cl

        def _try_click(btn_hwnd, btn_text):
            logging.info("Clicking button '%s' (HWND=%d)", btn_text, btn_hwnd)
            BM_CLICK = 0x00F5
            # SendMessage is synchronous — Quicken MUST process this before returning
            user32.SendMessageW(btn_hwnd, BM_CLICK, 0, 0)
            time.sleep(self.delay_dialog_action)
            # Verify the dialog actually closed
            if not user32.IsWindowVisible(dlg_hwnd):
                logging.info("Dialog closed after clicking '%s'", btn_text)
                return True
            # Dialog still visible — try WM_COMMAND as backup
            logging.info("Dialog still visible after BM_CLICK, trying WM_COMMAND")
            WM_COMMAND = 0x0111
            BN_CLICKED = 0
            ctrl_id = user32.GetDlgCtrlID(btn_hwnd)
            wparam = (BN_CLICKED << 16) | (ctrl_id & 0xFFFF)
            user32.SendMessageW(dlg_hwnd, WM_COMMAND, ctypes.c_void_p(wparam), ctypes.c_void_p(btn_hwnd))
            time.sleep(self.delay_dialog_action)
            if not user32.IsWindowVisible(dlg_hwnd):
                logging.info("Dialog closed after WM_COMMAND")
                return True
            logging.warning("Dialog still visible after clicking '%s'", btn_text)
            return True  # return True anyway — the click was sent

        # Search up to 3 levels deep
        stack = [dlg_hwnd]
        for _depth in range(3):
            next_stack = []
            for parent in stack:
                child = user32.GetWindow(parent, 5)  # GW_CHILD
                while child:
                    cls_buf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(child, cls_buf, 256)
                    cls_name = cls_buf.value

                    if _is_button(cls_name):
                        txt_buf = ctypes.create_unicode_buffer(256)
                        user32.GetWindowTextW(child, txt_buf, 256)
                        txt = txt_buf.value.lower()
                        logging.debug("  Found control: class='%s' text='%s'", cls_name, txt_buf.value)
                        if txt in titles:
                            return _try_click(child, txt_buf.value)

                    next_stack.append(child)
                    child = user32.GetWindow(child, 2)  # GW_HWNDNEXT
            stack = next_stack

        # Fallback: dump all controls and try clicking the first one found
        logging.info("Targeted button not found. Enumerating all buttons in dialog:")
        first_button = None
        child = user32.GetWindow(dlg_hwnd, 5)
        while child:
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child, cls_buf, 256)
            if _is_button(cls_buf.value):
                txt_buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(child, txt_buf, 256)
                logging.info("  Button: class='%s' text='%s' HWND=%d",
                             cls_buf.value, txt_buf.value, child)
                if first_button is None:
                    first_button = (child, txt_buf.value)
            child = user32.GetWindow(child, 2)

        if first_button:
            btn_hwnd, btn_text = first_button
            return _try_click(btn_hwnd, btn_text)

        # Last resort: SendMessage WM_KEYDOWN VK_RETURN to the dialog
        logging.info("No buttons found, sending Enter to dialog via SendMessage")
        WM_KEYDOWN = 0x0100
        WM_KEYUP = 0x0101
        VK_RETURN = 0x0D
        user32.SendMessageW(dlg_hwnd, WM_KEYDOWN, ctypes.c_void_p(VK_RETURN), ctypes.c_void_p(0))
        user32.SendMessageW(dlg_hwnd, WM_KEYUP, ctypes.c_void_p(VK_RETURN), ctypes.c_void_p(0))
        time.sleep(self.delay_dialog_action)
        return True

    def _wait_for_export_complete(self, dialog, output_file):
        """
        Wait for the export to complete.

        Verifies success by:
        1. Waiting for the expected QIF file to appear on disk
        2. Confirming the file size stabilizes (Quicken is done writing)
        3. Ensuring the export dialog has closed
        """
        deadline = time.time() + self.timeout
        safe_name = output_file.stem  # filename without extension
        output_dir = output_file.parent

        # Phase 1: wait for the QIF file to appear
        logging.info("Waiting for QIF file: %s", output_file.name)
        while time.time() < deadline:
            # Check both .qif and .QIF
            for ext in (".qif", ".QIF"):
                candidate = output_dir / f"{safe_name}{ext}"
                if candidate.exists() and candidate.stat().st_size > 0:
                    # File appeared — wait for size to stabilize
                    return self._wait_for_file_stable(candidate, deadline,
                                                     self._export_baseline_hwnds)
            time.sleep(self.delay_dialog_action)

        # Phase 2: if file never appeared, check if dialog closed anyway
        logging.warning("QIF file not found, checking if dialog closed")
        try:
            if not dialog.exists(timeout=2):
                logging.info("Export dialog closed (but file not found)")
                time.sleep(self.delay)
                return False
        except Exception:
            logging.info("Export dialog gone (but file not found)")
            time.sleep(self.delay)
            return False

        logging.warning("Timeout waiting for export to complete")
        self.close_dialog(dialog)
        return False

    def _wait_for_file_stable(self, filepath, deadline, baseline_hwnds):
        """
        Wait until the file size stops changing, indicating Quicken
        has finished writing it.
        """
        prev_size = -1
        stable_count = 0

        while time.time() < deadline:
            try:
                cur_size = filepath.stat().st_size
            except OSError:
                time.sleep(self.delay_keystroke)
                continue

            if cur_size == prev_size and cur_size > 0:
                stable_count += 1
                if stable_count >= 3:  # stable for ~1.5 seconds
                    logging.info("File stable: %s (%d bytes)", filepath.name, cur_size)
                    # Wait for Quicken to close its "Exporting QIF File" progress dialog
                    return self._wait_for_quicken_idle(baseline_hwnds)
            else:
                stable_count = 0
                prev_size = cur_size
                if cur_size > 0:
                    logging.debug("File growing: %s (%d bytes)", filepath.name, cur_size)

            time.sleep(self.delay_dialog_action)

        logging.warning("Timeout waiting for file to stabilize: %s", filepath.name)
        return False

    def _wait_for_quicken_idle(self, baseline_hwnds, max_wait=30):
        """
        Wait until Quicken has no NEW child/dialog windows open beyond
        the baseline captured before the export started.  This catches
        "Exporting QIF File" progress dialogs that linger after the file
        is written, while ignoring persistent windows like the main window
        and Quicken's "(untitled)" companion window.

        Returns False if Quicken still has extra windows open — we must
        NOT proceed as it will hang Quicken.
        """
        deadline = time.time() + max_wait
        while time.time() < deadline:
            hwnds = self._get_quicken_top_level_hwnds()
            new_hwnds = hwnds - baseline_hwnds
            if not new_hwnds:
                logging.info("Quicken idle (no new windows remain)")
                return True
            titles = []
            for h in new_hwnds:
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(h, buf, 256)
                titles.append(buf.value or "(untitled)")
            logging.info("Quicken still has %d new window(s): %s — waiting...",
                         len(new_hwnds), titles)
            time.sleep(self.delay_idle_poll)

        logging.error("Quicken still has open windows after %ds — "
                      "cannot proceed, export FAILED", max_wait)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Automate QIF exports from Quicken for all accounts."
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for QIF files (overrides config)",
    )
    parser.add_argument(
        "--accounts",
        default=None,
        help="Comma-separated list of accounts to export (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Only discover and list accounts, do not export (overrides config RUN_MODE)",
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        default=None,
        help="Discover and export all accounts (overrides config RUN_MODE)",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Re-export accounts even if QIF file already exists",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Seconds between account exports (overrides config)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Max seconds to wait for Quicken (overrides config)",
    )
    parser.add_argument(
        "--config",
        default="quicken_qif_export.config",
        help="Path to config file (default: quicken_qif_export.config)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--max",
        type=str,
        default=None,
        help="Max accounts to export (number or ALL). Overrides config MAX_EXPORTS.",
    )
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Load config
    config = load_config(args.config)

    # Apply CLI overrides
    output_dir = get_config_value(config, "OUTPUT_DIR", args.output_dir, "quicken_export_files")
    config["OUTPUT_DIR"] = output_dir

    if args.delay is not None:
        config["DELAY_BETWEEN_ACCOUNTS"] = str(args.delay)
    if args.timeout is not None:
        config["TIMEOUT"] = str(args.timeout)

    skip_existing = get_config_value(config, "SKIP_EXISTING", None, "true")
    if args.no_skip_existing:
        skip_existing = "false"

    # Determine run mode: CLI flags override config RUN_MODE
    run_mode = config.get("RUN_MODE", "dry-run").lower().strip()
    if args.prod:
        run_mode = "prod"
    elif args.dry_run:
        run_mode = "dry-run"
    dry_run = (run_mode != "prod")

    # Setup logging
    log_file = config.get("LOG_FILE", "quicken_export.log")
    setup_logging(log_file)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    logging.info("=" * 60)
    logging.info("Quicken QIF Auto-Export (mode: %s)", "dry-run" if dry_run else "prod")
    logging.info("=" * 60)

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    logging.info("Output directory: %s", output_path.resolve())

    # Initialize exporter
    exporter = QuickenQIFExporter(config)

    # Connect to Quicken
    if not exporter.connect():
        sys.exit(1)

    # Discover accounts
    accounts = exporter.discover_accounts()
    if not accounts:
        logging.error("No accounts discovered.  Check that Quicken is showing "
                       "the main window with accounts visible.")
        sys.exit(1)

    logging.info("Accounts found: %d", len(accounts))
    for i, acct in enumerate(accounts, 1):
        logging.info("  %3d. %s", i, acct)

    # Filter out excluded accounts
    exclude_str = config.get("EXCLUDE_ACCOUNTS", "")
    if exclude_str:
        excluded = [a.strip() for a in exclude_str.split(",") if a.strip()]
        before = len(accounts)
        accounts = [a for a in accounts if a not in excluded]
        if before - len(accounts):
            logging.info("Excluded %d account(s): %s", before - len(accounts), excluded)

    # Filter accounts if --accounts specified
    if args.accounts:
        requested = [a.strip() for a in args.accounts.split(",")]
        accounts = [a for a in accounts if a in requested]
        logging.info("Filtered to %d requested accounts", len(accounts))

    # Dry run — stop here
    if dry_run:
        logging.info("Dry run complete.  No exports performed.")
        return

    # Filter out already-exported accounts
    if skip_existing == "true":
        before = len(accounts)
        accounts = [
            a for a in accounts
            if not (output_path / f"{QuickenQIFExporter._sanitize_filename(a)}.qif").exists()
               and not (output_path / f"{QuickenQIFExporter._sanitize_filename(a)}.QIF").exists()
        ]
        skipped = before - len(accounts)
        if skipped:
            logging.info(
                "Skipping %d accounts that already have QIF files "
                "(use --no-skip-existing to re-export)", skipped
            )

    if not accounts:
        logging.info("All accounts already exported.  Nothing to do.")
        return

    # Apply MAX_EXPORTS limit
    max_exports_str = args.max if args.max is not None else config.get("MAX_EXPORTS", "ALL").strip().upper()
    if max_exports_str != "ALL":
        try:
            max_exports = int(max_exports_str)
            if max_exports < len(accounts):
                logging.info("Limiting to %d accounts (MAX_EXPORTS=%s)", max_exports, max_exports_str)
                accounts = accounts[:max_exports]
        except ValueError:
            logging.warning("Invalid MAX_EXPORTS value '%s' — exporting all accounts", max_exports_str)

    # Export each account
    logging.info("Will export %d accounts", len(accounts))
    exported = 0
    failed = []

    for i, account_name in enumerate(accounts, 1):
        logging.info("-" * 40)
        logging.info("[%d/%d] %s", i, len(accounts), account_name)

        try:
            success = exporter.export_account(account_name)
            if success:
                exported += 1
            else:
                logging.error("FAILED: %s — stopping to keep Quicken stable", account_name)
                failed.append(account_name)
                break
        except Exception as exc:
            logging.error("Exception exporting '%s': %s — stopping", account_name, exc)
            failed.append(account_name)
            break

    # Summary
    logging.info("=" * 60)
    logging.info("Export complete")
    logging.info("  Exported: %d", exported)
    logging.info("  Failed:   %d", len(failed))
    if failed:
        logging.info("  Failed accounts:")
        for acct in failed:
            logging.info("    - %s", acct)
    logging.info("=" * 60)


if __name__ == "__main__":
    main()
