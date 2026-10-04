#!/usr/bin/env python3
"""Developer tool: wrap user-visible string literals of UI calls with _() (i18n). Prints f-strings left to do by hand."""
import ast
import re
import sys

UI_CALLS = {"label", "button", "setToolTip", "setText", "setPlaceholderText", "addTab", "addItem", "addItems", "Friendly",
            "QLabel", "QCheckBox", "QGroupBox", "setWindowTitle", "emit", "_status", "start", "addRow", "addAction",
            "getText", "getItem", "getOpenFileName", "getSaveFileName", "show_toast", "set", "_Step", "setFormat",
            "_run_repair", "print", "append", "setItemData", "progress"}
SKIP_FUNCS = {"_", "pick", "setStyleSheet", "setObjectName", "connect", "insert", "QUrl", "join", "split", "format",
              "startswith", "endswith", "replace", "get", "QProcess", "guarded", "setProgram", "setData", "itemData",
              "navigate", "findData", "setCurrentIndex"}


def letters(s: str) -> bool:
    return bool(re.search(r"[A-Za-zÀ-ÿ]{2,}", s)) and not re.fullmatch(r"[\w./:-]+", s.strip())


def main(path: str) -> None:
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    lines = src.split("\n")
    offs = [0]
    for l in lines:
        offs.append(offs[-1] + len(l) + 1)

    def pos(lineno, col):
        return offs[lineno - 1] + len(lines[lineno - 1].encode()[:col].decode())

    edits, manual = [], []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def visit_Call(self, node):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            self.stack.append(name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Constant(self, node):
            if isinstance(node.value, str) and self.stack and letters(node.value):
                top = self.stack[-1]
                if top in UI_CALLS and not (set(self.stack) & SKIP_FUNCS):
                    edits.append((pos(node.lineno, node.col_offset), pos(node.end_lineno, node.end_col_offset)))

        def visit_JoinedStr(self, node):
            txt = ast.get_source_segment(src, node) or ""
            if self.stack and self.stack[-1] in UI_CALLS and re.search(r"[A-Za-zÀ-ÿ]{3,}", txt) and \
                    not (set(self.stack) & SKIP_FUNCS):
                manual.append(f"{path}:{node.lineno}: {txt[:120]}")
    V().visit(tree)
    for a, b in sorted(edits, reverse=True):
        src = src[:a] + "_(" + src[a:b] + ")" + src[b:]
    if edits and "from ..i18n import _" not in src and "from .i18n import _" not in src:
        imp = "from ..i18n import _\n" if "/ui/" in path else "from .i18n import _\n"
        src = re.sub(r"(\n(?:from \.\.? ?\S* import [^\n]+|from \.\S+ import [^\n]+)\n)", lambda m: m.group(1) + imp, src, count=1)
    open(path, "w", encoding="utf-8").write(src)
    print(f"{path}: {len(edits)} wrapped")
    for m in manual:
        print("  MANUAL", m)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
