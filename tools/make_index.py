import ast
import os

KIT = "<EDRIVE>/lygo-protocol-stack/lygo_llm_console"
OUT = os.path.join(os.environ["LOCALAPPDATA"], "Temp", "wp_index.md")
L = []


def first_line(doc):
    if not doc:
        return ""
    for ln in doc.strip().splitlines():
        ln = ln.strip()
        if ln:
            return ln[:150]
    return ""


def scan(sub):
    d = os.path.join(KIT, sub)
    if not os.path.isdir(d):
        return []
    rows = []
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if not os.path.isfile(p):
            continue
        size = os.path.getsize(p)
        if f.endswith(".py"):
            try:
                src = open(p, encoding="utf-8", errors="replace").read()
                tree = ast.parse(src)
            except Exception as e:
                rows.append((f, size, 0, "PARSE FAIL %s" % e, []))
                continue
            nlines = src.count("\n") + 1
            role = first_line(ast.get_docstring(tree))
            fns = []
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fns.append((node.name, first_line(ast.get_docstring(node))))
                elif isinstance(node, ast.ClassDef):
                    meth = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
                    fns.append(("class " + node.name, first_line(ast.get_docstring(node)) + (" | methods: " + ", ".join(meth[:12]) if meth else "")))
            rows.append((f, size, nlines, role, fns))
        else:
            rows.append((f, size, 0, "", []))
    return rows


for sub in ("src",):
    rows = scan(sub)
    tot = sum(r[2] for r in rows)
    L.append("### `%s/` — %d python modules, %d lines total" % (sub, len(rows), tot))
    L.append("")
    for f, size, nlines, role, fns in rows:
        L.append("**`%s/%s`** — %d lines / %d bytes%s" % (sub, f, nlines, size, (" — " + role) if role else ""))
        for name, doc in fns:
            L.append("  - `%s`%s" % (name, (" — " + doc) if doc else ""))
        L.append("")
    L.append("")

L.append("### `tests/` — test modules")
L.append("")
rows = scan("tests")
tot = 0
for f, size, nlines, role, fns in rows:
    tot += nlines
    L.append("- `tests/%s` — %d lines%s" % (f, nlines, (" — " + role) if role else ""))
L.append("")
L.append("tests total lines: %d" % tot)
L.append("")

for sub in ("portal", "web_portal", "config", "prompts", "scripts", "skills"):
    rows = scan(sub)
    if not rows:
        continue
    L.append("### `%s/`" % sub)
    L.append("")
    for f, size, nlines, role, fns in rows:
        L.append("- `%s/%s` — %d bytes%s" % (sub, f, size, (" — " + role) if role else ""))
    L.append("")

# top level
L.append("### kit root files")
L.append("")
for f in sorted(os.listdir(KIT)):
    p = os.path.join(KIT, f)
    if os.path.isfile(p):
        L.append("- `%s` — %d bytes" % (f, os.path.getsize(p)))
    else:
        L.append("- `%s/` (dir)" % f)

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(L) + "\n")
print("WROTE", OUT, len(L), "lines")
