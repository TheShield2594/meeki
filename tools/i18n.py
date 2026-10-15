#!/usr/bin/env python3
"""Translation workflow.

    python tools/i18n.py extract    # update po/pomodoro-pet.pot from the sources (needs xgettext)
    python tools/i18n.py update     # extract, then merge into every po/<lang>.po (needs msgmerge)
    python tools/i18n.py compile    # po/<lang>.po -> build/locale/<lang>/LC_MESSAGES/pomodoro-pet.mo
    python tools/i18n.py check      # CI: template up to date, every .po compiles

Start a new language:  cp po/pomodoro-pet.pot po/de.po  (then translate the msgstr lines)

`compile` is pure Python, so building the package doesn't need gettext.
"""

from __future__ import annotations

import ast
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PO_DIR = ROOT / "po"
POT = PO_DIR / "pomodoro-pet.pot"
DOMAIN = "pomodoro-pet"


def version() -> str:
    for line in (ROOT / "pyproject.toml").read_text().splitlines():
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    return "0"


def extract(out: Path = POT) -> None:
    if not shutil.which("xgettext"):
        sys.exit("xgettext not found (install gettext)")
    sources = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "pomodoro_pet").glob("*.py"))
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["xgettext", "--language=Python", "--from-code=UTF-8", "--keyword=_", "--keyword=ngettext:1,2",
         "--add-comments=Translators:", "--sort-by-file", "--add-location=file", f"--package-name={DOMAIN}",
         f"--package-version={version()}", "-o", str(out), *sources],
        cwd=ROOT, check=True,
    )


# ---- minimal msgfmt -------------------------------------------------------
def parse_po(path: Path) -> dict[str, str]:
    """{msgid (with \\0-joined plural): msgstr (\\0-joined plural forms)}; fuzzy and
    untranslated entries are skipped, like msgfmt does."""
    catalog: dict[str, str] = {}
    entry: dict[str, object] = {}
    fuzzy = False
    field: str | None = None

    def flush() -> None:
        nonlocal entry, fuzzy
        if "msgid" in entry:
            msgid = entry["msgid"]
            if "msgid_plural" in entry:
                msgid = f"{msgid}\0{entry['msgid_plural']}"
                forms = entry.get("plural", {})
                msgstr = "\0".join(forms[k] for k in sorted(forms))
            else:
                msgstr = entry.get("msgstr", "")
            if msgstr.replace("\0", "") and (not fuzzy or msgid == ""):
                catalog[msgid] = msgstr
        entry, fuzzy = {}, False

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#,") and "fuzzy" in line:
            fuzzy = True
            continue
        if not line or line.startswith("#"):
            if not line and entry:
                flush()
            continue
        if line.startswith("msgid_plural "):
            field = "msgid_plural"
            entry[field] = ast.literal_eval(line[13:])
        elif line.startswith("msgid "):
            if "msgstr" in entry or "plural" in entry:
                flush()
            field = "msgid"
            entry[field] = ast.literal_eval(line[6:])
        elif line.startswith("msgstr["):
            index = int(line[7:line.index("]")])
            field = f"plural:{index}"
            entry.setdefault("plural", {})[index] = ast.literal_eval(line[line.index("]") + 1:].strip())
        elif line.startswith("msgstr "):
            field = "msgstr"
            entry[field] = ast.literal_eval(line[7:])
        elif line.startswith('"') and field:
            text = ast.literal_eval(line)
            if field.startswith("plural:"):
                index = int(field.split(":")[1])
                entry["plural"][index] += text
            else:
                entry[field] += text
    flush()
    return catalog


def write_mo(catalog: dict[str, str], out: Path) -> None:
    keys = sorted(catalog)
    ids = b""
    strs = b""
    offsets = []
    for key in keys:
        k, v = key.encode("utf-8"), catalog[key].encode("utf-8")
        offsets.append((len(ids), len(k), len(strs), len(v)))
        ids += k + b"\0"
        strs += v + b"\0"
    n = len(keys)
    key_start = 7 * 4 + 16 * n
    value_start = key_start + len(ids)
    table = []
    for o1, l1, _o2, _l2 in offsets:
        table += [l1, o1 + key_start]
    for _o1, _l1, o2, l2 in offsets:
        table += [l2, o2 + value_start]
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as fh:
        fh.write(struct.pack("Iiiiiii", 0x950412DE, 0, n, 7 * 4, 7 * 4 + n * 8, 0, 0))
        fh.write(struct.pack(f"{len(table)}i", *table))
        fh.write(ids)
        fh.write(strs)


def compile_all(out_dir: Path = ROOT / "build" / "locale") -> list[Path]:
    written = []
    for po in sorted(PO_DIR.glob("*.po")):
        mo = out_dir / po.stem / "LC_MESSAGES" / f"{DOMAIN}.mo"
        write_mo(parse_po(po), mo)
        written.append(mo)
    return written


def _strip_dates(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if "POT-Creation-Date" not in line)


def check() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "fresh.pot"
        extract(fresh)
        if not POT.exists() or _strip_dates(fresh.read_text()) != _strip_dates(POT.read_text()):
            print("po/pomodoro-pet.pot is out of date: run `python tools/i18n.py extract`", file=sys.stderr)
            return 1
        compiled = compile_all(Path(tmp) / "locale")
    print(f"Template up to date; {len(compiled)} translation(s) compile.")
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "extract":
        extract()
    elif command == "update":
        extract()
        for po in sorted(PO_DIR.glob("*.po")):
            subprocess.run(["msgmerge", "--update", "--backup=none", str(po), str(POT)], check=True)
    elif command == "compile":
        for mo in compile_all():
            print(mo.relative_to(ROOT))
    elif command == "check":
        return check()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
