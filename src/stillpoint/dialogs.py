"""Modal dialog helpers.

Wrappers around tkinter's built-in dialogs, themed to the app. Uses the tkinter
'simpledialog' and 'messagebox' facilities; these are only imported when a
dialog is actually shown so headless tests never touch tkinter.
"""

from __future__ import annotations

from tkinter import simpledialog, messagebox


def ask_string(title: str, prompt: str, parent=None, initial: str = "") -> str | None:
    """Ask for a string; returns the value or None if cancelled."""
    return simpledialog.askstring(title, prompt, parent=parent, initialvalue=initial)


def ask_integer(title: str, prompt: str, parent=None, initial: int = 0, minvalue: int | None = None, maxvalue: int | None = None) -> int | None:
    """Ask for an integer; returns the value or None if cancelled."""
    return simpledialog.askinteger(title, prompt, parent=parent, initialvalue=initial, minvalue=minvalue, maxvalue=maxvalue)


def info(title: str, message: str, parent=None) -> None:
    messagebox.showinfo(title, message, parent=parent)


def warn(title: str, message: str, parent=None) -> None:
    messagebox.showwarning(title, message, parent=parent)


def error(title: str, message: str, parent=None) -> None:
    messagebox.showerror(title, message, parent=parent)


def ask_ok_cancel(title: str, message: str, parent=None) -> bool:
    """Ask to continue; True when the user confirms."""
    return messagebox.askokcancel(title, message, parent=parent)


def ask_yes_no(title: str, message: str, parent=None) -> bool:
    """Ask a yes/no question; True when the user says yes."""
    return messagebox.askyesno(title, message, parent=parent)
