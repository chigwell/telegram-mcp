#!/usr/bin/env python3
"""Rebuild Telethon's TL classes against the current MTProto layer.

Why
---
Telethon releases lag behind production Telegram. As of 2026-08-11 the newest
release (1.44.0, published 2026-06-15) is built for LAYER 227, while the server
already answers with LAYER 228 objects, where the `user` constructor changed
(0x31774388 -> 0xb1b8cc83).

A client that does not know a constructor does not merely lose one field — the
whole read buffer desynchronises. Outwards that shows up as `TypeNotFoundError`
carrying an id that exists in no schema at all (garbage read past the
desync), and only *some* calls break while their neighbours keep working:
`list_chats`, `get_common_chats`, `resolve_username` and `get_full_user` fail
while `get_chats`, `search_contacts`, `list_messages` and `send_message` are
fine. Upgrading does not help — 1.44.0 is already the latest published release.

What this does
--------------
Takes the vendored schema (`vendor/api-layer228.tl`, lifted from the Telegram
Desktop dev branch) and the vendored Telethon generator, rebuilds
`telethon/tl/{types,functions,alltlobjects.py}` from them and drops the result
over the installed package. The rest of the library — client, networking,
custom classes — stays exactly as shipped; only generated TL classes change.

Usage
-----
    uv run python scripts/patch_telethon_layer.py            # apply
    uv run python scripts/patch_telethon_layer.py --check    # compare layers
    uv run python scripts/patch_telethon_layer.py --restore  # roll back

A backup of the original is written next to the package on first run, so
rolling back needs no reinstall.

NOTE: already running processes keep the old code in memory — only newly
started ones pick the patch up. Restart your MCP clients after applying.

When Telethon ships a release for the current layer, drop the vendored schema
and this script.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENDOR_SCHEMA = REPO / "vendor" / "api-layer228.tl"
VENDOR_GENERATOR = REPO / "vendor" / "telethon_generator"
GENERATED = ("types", "functions", "alltlobjects.py")


def telethon_tl_dir() -> Path:
    """The tl/ directory of the installed telethon — the one the server actually runs."""
    import telethon

    return Path(telethon.__file__).parent / "tl"


def current_layer() -> tuple[int, str]:
    """Layer of the installed telethon — MUST be read in a separate process.

    In our own process `telethon.tl` is already imported, so after swapping the
    files Python keeps serving the cached module: the check would report the old
    layer and claim the patch had failed on a patch that in fact succeeded.
    """
    code = (
        "from telethon.tl.alltlobjects import LAYER;"
        "from telethon.tl.types import User;"
        "print(LAYER, hex(User.CONSTRUCTOR_ID))"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"could not read the telethon layer:\n{res.stderr}")
    layer, ctor = res.stdout.split()
    return int(layer), ctor


def schema_layer() -> int:
    for line in VENDOR_SCHEMA.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("// LAYER "):
            return int(line.strip().rsplit(" ", 1)[1])
    raise SystemExit(f"no '// LAYER N' line in {VENDOR_SCHEMA}")


def backup_path(tl_dir: Path, layer: int) -> Path:
    return tl_dir.parent / f"tl.orig-layer{layer}.tar.gz"


def do_check() -> int:
    layer, user_ctor = current_layer()
    want = schema_layer()
    print(f"installed: LAYER {layer}, user# = {user_ctor}")
    print(f"vendored schema: LAYER {want}")
    if layer == want:
        print("match — patch is applied")
        return 0
    print("mismatch — patch needed (see module docstring)")
    return 1


def do_restore() -> int:
    tl_dir = telethon_tl_dir()
    backups = sorted(tl_dir.parent.glob("tl.orig-layer*.tar.gz"))
    if not backups:
        raise SystemExit(
            "no backup — roll back by reinstalling: uv sync --reinstall-package telethon"
        )
    archive = backups[-1]
    for name in GENERATED:
        target = tl_dir / name
        shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
    with tarfile.open(archive) as tar:
        # filter="data" is the Python 3.14 default; set explicitly to avoid the warning
        tar.extractall(tl_dir.parent, filter="data")
    _drop_pycache(tl_dir.parent)
    print(f"restored from {archive.name}")
    return do_check()


def _drop_pycache(root: Path) -> None:
    for cache in root.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def do_patch() -> int:
    if not VENDOR_SCHEMA.exists() or not VENDOR_GENERATOR.exists():
        raise SystemExit("missing vendor/api-layer228.tl or vendor/telethon_generator")

    tl_dir = telethon_tl_dir()
    layer_before, user_before = current_layer()
    want = schema_layer()
    if layer_before == want:
        print(f"already LAYER {want} — nothing to do")
        return 0

    backup = backup_path(tl_dir, layer_before)
    if not backup.exists():
        with tarfile.open(backup, "w:gz") as tar:
            for name in GENERATED:
                tar.add(tl_dir / name, arcname=f"tl/{name}")
        print(f"backup of the original: {backup}")

    # The Telethon generator writes relative to itself, so build in a temp tree.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copytree(VENDOR_GENERATOR, work / "telethon_generator")
        shutil.copy(VENDOR_SCHEMA, work / "telethon_generator" / "data" / "api.tl")
        (work / "telethon" / "tl").mkdir(parents=True, exist_ok=True)

        gen = work / "gen.py"
        gen.write_text(
            "import sys, itertools; sys.path.insert(0, '.')\n"
            "from pathlib import Path\n"
            "from telethon_generator.parsers import parse_tl, find_layer, parse_errors, parse_methods\n"
            "from telethon_generator.generators import generate_tlobjects\n"
            "gen = Path('telethon_generator')\n"
            "tls = sorted(gen.glob('data/*.tl'))\n"
            "layer = next(filter(None, map(find_layer, tls)))\n"
            "errors = list(parse_errors(gen / 'data/errors.csv'))\n"
            "methods = list(parse_methods(gen / 'data/methods.csv', gen / 'data/friendly.csv',\n"
            "                             {e.str_code: e for e in errors}))\n"
            "objs = list(itertools.chain(*(parse_tl(f, layer, methods) for f in tls)))\n"
            "generate_tlobjects(objs, layer, 2, Path('telethon/tl'))\n"
            "print('LAYER', layer)\n",
            encoding="utf-8",
        )
        res = subprocess.run([sys.executable, "gen.py"], cwd=work, capture_output=True, text=True)
        if res.returncode != 0:
            raise SystemExit(f"generation failed:\n{res.stdout}\n{res.stderr}")

        built = work / "telethon" / "tl"
        missing = [n for n in GENERATED if not (built / n).exists()]
        if missing:
            raise SystemExit(f"generator did not produce: {missing}")

        for name in GENERATED:
            target = tl_dir / name
            shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
            src = built / name
            shutil.copytree(src, target) if src.is_dir() else shutil.copy(src, target)

    _drop_pycache(tl_dir.parent)
    print(f"was: LAYER {layer_before}, user# = {user_before}")
    return do_check()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="show installed vs vendored layer")
    g.add_argument("--restore", action="store_true", help="roll back from the backup")
    args = ap.parse_args()
    if args.check:
        return do_check()
    if args.restore:
        return do_restore()
    return do_patch()


if __name__ == "__main__":
    raise SystemExit(main())
