#!/usr/bin/env python3
"""Add or remove an extension UUID in org.gnome.shell enabled-extensions.

Usage: enabled-extensions.py add|remove UUID
Used by install-extension.sh and uninstall.sh so the extension is enabled on the
next login even when the running shell has not indexed it yet.
"""
import ast
import subprocess
import sys


def main() -> int:
    action, uuid = sys.argv[1], sys.argv[2]
    out = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                         capture_output=True, text=True).stdout.strip()
    try:
        current = list(ast.literal_eval(out.replace("@as ", ""))) if out and out != "@as []" else []
    except (ValueError, SyntaxError):
        current = []
    wanted = [u for u in current if u != uuid] + ([uuid] if action == "add" else [])
    if wanted != current:
        subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(wanted)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
