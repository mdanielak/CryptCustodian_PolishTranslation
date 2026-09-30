#!/usr/bin/env python3
"""Offline SteamOS/Linux installer for the C-Force package.

It deliberately trusts only the release manifest pinned into this file by the
builder.  Game files are observed transactionally; their shipping hashes are
not a compatibility gate.
"""
import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import uuid
from pathlib import Path

try:
    import fcntl
except ImportError:  # Host-side syntax/unit tests on Windows; execution is Linux-only.
    fcntl = None

APPID = "2394650"
PAYLOAD_NAMES = ("data.win", "translations.ini")
MANIFEST_SHA256 = "__MANIFEST_SHA256__"
ROOT = Path(__file__).resolve().parent
PACKAGE_FILE_LIST = ("CZYTAJ_MNIE.txt", "GameFiles/data.win", "GameFiles/translations.ini", "INSTALUJ.sh", "LICENSE-NOTICE.txt", "ODINSTALUJ.sh", "SHA256SUMS.txt", "c_force_installer.py", "instaluj.bat", "instaluj.ps1", "odinstaluj.bat", "odinstaluj.ps1", "release-manifest.json", "selftest_c.ps1")
PACKAGE_FILES = set(PACKAGE_FILE_LIST)


class Refusal(RuntimeError):
    pass


def sha256(path):
    safe_existing(path, file=True)
    h = hashlib.sha256()
    with open(path, "rb", buffering=0) as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def safe_existing(path, file=False, directory=False):
    path = Path(path)
    if not path.is_absolute():
        raise Refusal("Wymagana jest ścieżka bezwzględna.")
    for item in (path, *path.parents):
        try:
            mode = os.lstat(item).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise Refusal("Symlink/reparse lub nietypowy obiekt: " + str(item))
    mode = os.lstat(path).st_mode
    if file and not stat.S_ISREG(mode):
        raise Refusal("To nie jest zwykły plik: " + str(path))
    if directory and not stat.S_ISDIR(mode):
        raise Refusal("To nie jest katalog: " + str(path))
    return path


def safe_child(parent, name):
    if name != Path(name).name or name in ("", ".", ".."):
        raise Refusal("Niedozwolona nazwa pliku")
    parent = safe_existing(parent, directory=True)
    return parent / name


def write_new_json(path, value):
    path = Path(path)
    safe_existing(path.parent, directory=True)
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    fsync_dir(path.parent)


def atomic_json(path, value):
    """Only replaces installer-owned receipt/journal files."""
    tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    write_new_json(tmp, value)
    os.replace(tmp, path)
    fsync_dir(path.parent)


def copy_verified(source, destination, expected):
    safe_existing(source, file=True)
    safe_existing(destination.parent, directory=True)
    if sha256(source) != expected:
        raise Refusal("Niezgodny payload/backup: " + str(source))
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with open(source, "rb", buffering=0) as src, os.fdopen(fd, "wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024); dst.flush(); os.fsync(dst.fileno())
    if sha256(destination) != expected:
        raise Refusal("Kopia nie przeszła weryfikacji")
    fsync_dir(destination.parent)


def file_record(path):
    safe_existing(path, file=True)
    return {"sha256": sha256(path), "bytes": path.stat().st_size}


def pair(game):
    return {name: file_record(safe_child(game, name)) for name in PAYLOAD_NAMES}


def parse_vdf_libraries(vdf):
    """Conservative parser: extracts quoted values only, never evaluates VDF."""
    try:
        text = safe_existing(vdf, file=True).read_text("utf-8", errors="replace")
    except (FileNotFoundError, Refusal):
        return []
    import re
    values = re.findall(r'"path"\s*"((?:\\\\.|[^"\\])*)"', text, re.I)
    return [Path(v.replace("\\\\", "\\")) for v in values]


def candidates(home):
    roots = [home / ".steam" / "steam", home / ".local" / "share" / "Steam",
             home / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam"]
    libraries = []
    for root in roots:
        libraries.append(root)
        libraries.extend(parse_vdf_libraries(root / "steamapps" / "libraryfolders.vdf"))
    for base in (Path("/run/media"), Path("/media")):
        if base.is_dir() and not base.is_symlink():
            for child in base.rglob("steamapps"):
                if len(child.parts) - len(base.parts) <= 3 and not child.is_symlink():
                    libraries.append(child.parent)
    found = set()
    for library in libraries:
        game = library / "steamapps" / "common" / "Crypt Custodian"
        appmanifest = library / "steamapps" / ("appmanifest_" + APPID + ".acf")
        try:
            safe_existing(game, directory=True)
            safe_existing(appmanifest, file=True)
            if APPID not in appmanifest.read_text("utf-8", errors="replace"):
                continue
            found.add(str(game.resolve(strict=True)))
        except (FileNotFoundError, Refusal):
            pass
    return sorted(found)


def package_manifest():
    # Closed package list rejects smuggled launchers/scripts before parsing JSON.
    actual = set()
    for current, dirs, files in os.walk(ROOT, followlinks=False):
        for name in dirs + files:
            p = Path(current) / name
            if p.is_symlink() or not (p.is_dir() or p.is_file()):
                raise Refusal("Niedozwolony obiekt paczki: " + str(p))
        actual.update((Path(current) / f).relative_to(ROOT).as_posix() for f in files)
    if actual != PACKAGE_FILES:
        raise Refusal("Paczka nie ma zamkniętego manifestu plików")
    sums = (ROOT / "SHA256SUMS.txt").read_text("ascii").splitlines()
    expected_sums = []
    for name in sorted(PACKAGE_FILES - {"SHA256SUMS.txt"}):
        expected_sums.append(sha256(ROOT / name) + "  " + name)
    if sums != expected_sums:
        raise Refusal("SHA256SUMS paczki jest niezgodny")
    manifest = ROOT / "release-manifest.json"
    if sha256(manifest) != MANIFEST_SHA256:
        raise Refusal("Manifest paczki niezgodny z launcherem")
    data = json.loads(manifest.read_text("utf-8"))
    if data.get("profile") != "C-Force" or str(data.get("appId")) != APPID or set(x.get("name") for x in data.get("payload", [])) != set(PAYLOAD_NAMES):
        raise Refusal("Manifest C-Force jest niezgodny")
    descriptors = {x["name"]: x for x in data["payload"]}
    for name in PAYLOAD_NAMES:
        if sha256(ROOT / "GameFiles" / name) != descriptors[name]["sha256"]:
            raise Refusal("Niezgodny payload paczki: " + name)
    return data, descriptors


def state_root(home):
    configured = os.environ.get("XDG_STATE_HOME")
    root = (Path(configured) if configured else home / ".local" / "state") / "crypt-custodian-polish-c-force"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe_existing(root, directory=True)
    return root


def make_dir(parent):
    safe_existing(parent, directory=True)
    p = parent / uuid.uuid4().hex
    os.mkdir(p, 0o700); fsync_dir(parent)
    return safe_existing(p, directory=True)


def incomplete(state, game):
    for kind in ("backups", "operations"):
        parent = state / kind
        if not parent.exists(): continue
        safe_existing(parent, directory=True)
        for child in parent.iterdir():
            if child.is_symlink() or not child.is_dir(): raise Refusal("Nietypowy wpis stanu")
            receipt, complete = child / "receipt.json", child / "complete"
            if not receipt.is_file() or not complete.is_file() or complete.read_text("ascii").strip() != sha256(receipt):
                raise Refusal("Wykryto niedokończoną transakcję; odmowa automatycznej operacji")


def replace_verified(stage, target, before, after):
    if file_record(target) != before or file_record(stage)["sha256"] != after:
        raise Refusal("TOCTOU przed os.replace")
    os.replace(stage, target)
    fsync_dir(target.parent)
    if file_record(target)["sha256"] != after:
        raise Refusal("Błąd readback po os.replace")


def install(game, state, manifest, desc):
    backups = state / "backups"; backups.mkdir(mode=0o700, exist_ok=True)
    before = pair(game); tx = make_dir(backups)
    receipt = {"schema":"ccpl-c-force/v1", "status":"intent", "appId":APPID, "version":manifest.get("version"),
               "gamePath":str(game), "transactionId":tx.name, "before":before, "after":None}
    write_new_json(tx / "receipt.json", receipt)
    for name in PAYLOAD_NAMES: copy_verified(game / name, tx / name, before[name]["sha256"])
    stage = make_dir(game)
    done = []
    try:
        for name in PAYLOAD_NAMES: copy_verified(ROOT / "GameFiles" / name, stage / name, desc[name]["sha256"])
        if pair(game) != before: raise Refusal("TOCTOU: gra zmieniła się przed commit")
        for name in PAYLOAD_NAMES:
            replace_verified(stage / name, game / name, before[name], desc[name]["sha256"]); done.append(name)
        receipt.update(status="committed", after=pair(game)); atomic_json(tx / "receipt.json", receipt)
        (tx / "complete").write_text(sha256(tx / "receipt.json"), encoding="ascii"); fsync_dir(tx)
    except Exception:
        for name in reversed(done):
            if file_record(game / name)["sha256"] != desc[name]["sha256"]: raise Refusal("Unknown: rollback nie nadpisze pliku")
            rollback = stage / ("rollback-" + name)
            copy_verified(tx / name, rollback, before[name]["sha256"])
            replace_verified(rollback, game / name, {"sha256":desc[name]["sha256"], "bytes":file_record(game / name)["bytes"]}, before[name]["sha256"])
        raise


def uninstall(game, state):
    choices = []
    for tx in (state / "backups").iterdir() if (state / "backups").is_dir() else ():
        receipt = tx / "receipt.json"
        if (tx / "complete").is_file() and (tx / "complete").read_text("ascii").strip() == sha256(receipt):
            r = json.loads(receipt.read_text("utf-8"))
            if r.get("gamePath") == str(game) and r.get("status") == "committed": choices.append((tx, r))
    if not choices: raise Refusal("Brak kompletnej kopii zapasowej")
    tx, r = sorted(choices, key=lambda x: x[0].name)[-1]
    if pair(game) != r["after"]: raise Refusal("Unknown: uninstall wymaga dokładnego after")
    for name in PAYLOAD_NAMES:
        if file_record(tx / name) != r["before"][name]: raise Refusal("Backup jest niekompletny")
    operations = state / "operations"; operations.mkdir(mode=0o700, exist_ok=True)
    operation = make_dir(operations); write_new_json(operation / "receipt.json", {**r, "status":"intent", "transactionId":operation.name})
    stage = make_dir(game)
    for name in PAYLOAD_NAMES: copy_verified(tx / name, stage / name, r["before"][name]["sha256"])
    for name in PAYLOAD_NAMES: replace_verified(stage / name, game / name, r["after"][name], r["before"][name]["sha256"])
    atomic_json(operation / "receipt.json", {**r, "status":"committed", "transactionId":operation.name})
    (operation / "complete").write_text(sha256(operation / "receipt.json"), encoding="ascii"); fsync_dir(operation)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("install", "uninstall", "preflight"), nargs="?", default="install")
    ap.add_argument("--game", type=Path); ap.add_argument("--yes", action="store_true")
    ap.add_argument("--noninteractive", action="store_true"); ap.add_argument("--accept-version-risk", action="store_true")
    args = ap.parse_args(); home_value = os.environ.get("HOME") or str(Path.home())
    home = Path(home_value).expanduser()
    manifest, desc = package_manifest()
    hits = [str(args.game.absolute())] if args.game else candidates(home)
    if len(hits) != 1: raise Refusal("Wiele/brak instalacji Steam; podaj --game")
    game = safe_existing(Path(hits[0]), directory=True)
    for name in PAYLOAD_NAMES: safe_existing(safe_child(game, name), file=True)
    if args.action == "preflight": print(json.dumps({"gamePath":str(game), "before":pair(game)})); return
    if args.action == "install" and not args.accept_version_risk: raise Refusal("Install wymaga --accept-version-risk")
    state = state_root(home)
    if fcntl is None: raise Refusal("Ten instalator wymaga Linux/SteamOS (flock).")
    with open(state / "installer.lock", "a+") as lock:
        getattr(fcntl, "flock")(lock, getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB")); incomplete(state, game)
        if args.action == "install": install(game, state, manifest, desc)
        else: uninstall(game, state)


if __name__ == "__main__":
    try: main()
    except (Refusal, OSError, json.JSONDecodeError) as exc:
        print("Błąd: " + str(exc), file=sys.stderr); sys.exit(1)
