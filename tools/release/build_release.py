"""Offline packaging only; never installs or modifies translation data.

--candidate: fresh temp package for PS5.1 tests, no final release.
--build: no-clobber final release plus deterministic, independently audited ZIP.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path

from verify_sources import (EXE, NAME, ORIGINAL, PATCHED, TEMP, VERSION, digest,
                            input_arguments, private_path, sources_from_args, verify)

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
FILES = sorted([
    "CZYTAJ_MNIE.txt", "instaluj.bat", "instaluj.ps1", "odinstaluj.bat",
    "odinstaluj.ps1", "GameFiles/data.win", "GameFiles/translations.ini",
    "release-manifest.json", "SHA256SUMS.txt", "LICENSE-NOTICE.txt",
])


def write_new(path, data):
    with path.open("xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def text_bytes(text, bom=False):
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8-sig" if bom else "utf-8")


def permission_metadata():
    # Preserve the complete English quotation, including paragraph breaks.
    notice = (HERE / "LICENSE-NOTICE.txt").read_text(encoding="utf-8-sig")
    start = 'CYTAT ANGIELSKI — TREŚĆ NADRZĘDNA\n\n"'
    end = '"\n\nPOLSKIE PODSUMOWANIE — WYŁĄCZNIE OBJAŚNIENIE'
    assert notice.count(start) == notice.count(end) == 1
    quote = notice.split(start, 1)[1].split(end, 1)[0]
    return {
        "basis": "user-supplied-permission-message",
        "claimedSenderRole": "developer and copyright holder of Crypt Custodian",
        "quote": quote, "authoritativeLanguage": "en",
        "fullQuoteLocation": "LICENSE-NOTICE.txt",
        "polishSummaryIsExplanatoryOnly": True,
        "independentlyVerifiedIdentity": False,
        "independentlyVerifiedAuthorship": False,
        "independentlyVerifiedMessageAuthenticity": False,
        "broaderLicenseInferred": False,
        "scopeNote": "Cytat wprost wymienia data.win; nie wymienia osobno translations.ini. Lista plików paczki nie rozszerza treści zgody.",
        "conditions": {
            "freeOfCharge": True, "clearlyUnofficialFanMade": True,
            "noOwnershipClaimToOriginalGameOrAssets": True,
            "modifiedFilesOnlyWithLegallyObtainedCopies": True,
        },
        "polishConditionsExplanation": [
            "Projekt jest rozpowszechniany bezpłatnie.",
            "Należy jasno wskazać, że to nieoficjalna, fanowska modyfikacja.",
            "Nie wolno rościć sobie praw własności do oryginalnej gry ani jej zasobów.",
            "Zmodyfikowanych plików wolno używać wyłącznie w połączeniu z legalnie uzyskanymi kopiami Crypt Custodian.",
        ],
    }


def prepare(target, sources):
    inputs = verify(sources)  # fresh validation BEFORE directory reservation/build
    target.mkdir(exist_ok=False)
    (target / "GameFiles").mkdir()
    manifest = {
        "schema": "crypt-custodian-polish-release/v1", "version": VERSION,
        "status": "public-beta", "technicalReadiness": "ready",
        "distributionPermission": permission_metadata(),
        "legalCopyRequired": True, "fanTranslation": True,
        "rightsNotice": "Gra, jej zasoby i znaki towarowe należą do odpowiednich właścicieli.",
        "appId": "2394650", "engine": "GameMaker/YYC",
        "platform": "Steam Windows x64", "languageSelection": "English",
        "uncertainEntries": 125, "exeSha256": EXE,
        "payload": [dict(name=n, originalSha256=ORIGINAL[n], **inputs[n]) for n in PATCHED],
    }
    manifest_bytes = text_bytes(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n")
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    write_new(target / "release-manifest.json", manifest_bytes)
    for n, source in sources.items():
        with source.open("rb") as src, (target / "GameFiles" / n).open("xb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        assert digest(target / "GameFiles" / n) == PATCHED[n]
    installer = (HERE / "installer.ps1").read_text(encoding="utf-8-sig")
    installer = installer.replace("__MANIFEST_SHA256__", manifest_hash)
    installer = installer.replace("__SELF_TEST_FUNCTIONS__", (HERE / "selftest.ps1").read_text(encoding="utf-8-sig"))
    assert "__MANIFEST_SHA256__" not in installer and "__SELF_TEST_FUNCTIONS__" not in installer
    write_new(target / "instaluj.ps1", text_bytes(installer, bom=True))
    for n in ["odinstaluj.ps1", "CZYTAJ_MNIE.txt", "LICENSE-NOTICE.txt"]:
        write_new(target / n, text_bytes((HERE / n).read_text(encoding="utf-8-sig"), bom=True))
    for action in ["instaluj", "odinstaluj"]:
        write_new(target / f"{action}.bat", bat_bytes(action))
    sums = "".join(f"{digest(target / n)}  {n}\n" for n in FILES if n != "SHA256SUMS.txt")
    write_new(target / "SHA256SUMS.txt", text_bytes(sums))
    audit_directory(target)
    return manifest


def bat_bytes(action):
    assert action in ("instaluj", "odinstaluj")
    return text_bytes(f'''@echo off
setlocal DisableDelayedExpansion
rem Crypt Custodian PL {VERSION}. Bypass applies only to this PowerShell process.
rem No system policy changes or game security bypass. Verify trusted ZIP SHA first.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0{action}.ps1" %*
set "rc=%errorlevel%"
if "%~1"=="" pause
exit /b %rc%
''')


def validate_names(names):
    seen = set()
    for name in names:
        if (not name or "\\" in name or ":" in name or name.startswith("/")
                or any(x in ("", ".", "..") or x.endswith((" ", ".")) for x in name.split("/"))):
            raise ValueError("Unsafe ZIP entry: " + name)
        folded = name.casefold()
        if folded in seen:
            raise ValueError("ZIP case collision: " + name)
        seen.add(folded)
    if sorted(names) != sorted(NAME + "/" + n for n in FILES):
        raise ValueError("ZIP closed allowlist mismatch")


def audit_directory(root):
    actual = []
    for current, dirs, files in os.walk(root, followlinks=False):
        for n in dirs + files:
            p = Path(current) / n
            if p.is_symlink() or p.is_junction():
                raise ValueError("Reparse in package")
        actual.extend((Path(current) / n).relative_to(root).as_posix() for n in files)
    if sorted(actual) != FILES:
        raise ValueError("Package closed allowlist mismatch")
    lines = (root / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
    expected = [f"{digest(root / n)}  {n}" for n in FILES if n != "SHA256SUMS.txt"]
    if lines != expected:
        raise ValueError("SHA256SUMS mismatch")
    for n in FILES:
        if n.startswith("GameFiles/"):
            continue
        text = (root / n).read_text(encoding="utf-8-sig").lower()
        if private_path(text):
            raise ValueError("Private build path in public package")


def make_zip(root, target):
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9, strict_timestamps=True) as z:
        for name in FILES:
            info = zipfile.ZipInfo(NAME + "/" + name, (2026, 9, 28, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.compress_level = 9
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            with (root / name).open("rb") as src, z.open(info, "w") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
    with target.open("r+b") as f:
        os.fsync(f.fileno())


def audit_zip(root, target):
    checks = []
    with zipfile.ZipFile(target) as z:
        validate_names(z.namelist())
        assert z.namelist() == [NAME + "/" + n for n in FILES]
        for n, info in zip(FILES, z.infolist()):
            assert not info.is_dir() and not (info.flag_bits & 1)
            assert info.date_time == (2026, 9, 28, 0, 0, 0)
            assert (info.external_attr >> 16) == 0o100644
            h = hashlib.sha256()
            count = 0
            with z.open(info) as archived, (root / n).open("rb") as source:
                while True:
                    a = archived.read(1024 * 1024)  # zipfile checks CRC at EOF
                    b = source.read(1024 * 1024)
                    if a != b:
                        raise ValueError("ZIP byte mismatch: " + n)
                    if not a:
                        break
                    h.update(a)
                    count += len(a)
            assert count == info.file_size
            assert h.hexdigest() == digest(root / n)
            checks.append(dict(name=n, sha256=h.hexdigest(), bytes=count, crc32=f"{info.CRC:08x}"))
    return checks


def test_zip_validator():
    good = [NAME + "/" + n for n in FILES]
    validate_names(good)
    bad_cases = ["../evil", "/absolute", "C:/evil", NAME + "/x:ads", NAME + "/../evil",
                 NAME + "/a\\b", NAME + "/a.", NAME + "//a", NAME + "/extra.dll"]
    for bad in bad_cases:
        try:
            validate_names(good + [bad])
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    for bad in [good[0], good[0].swapcase()]:
        try:
            validate_names(good + [bad])
        except ValueError:
            pass
        else:
            raise AssertionError("collision")
    print("ZIP name validator: 12 checks OK")


def main():
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--candidate", action="store_true")
    modes.add_argument("--build", action="store_true")
    input_arguments(parser)
    parser.add_argument("--output-root", type=Path,
                        default=os.environ.get("CC_RELEASE_OUTPUT_ROOT", str(TEMP)),
                        help="Existing external parent; package and ZIP use no-clobber creation.")
    args = parser.parse_args()
    sources = sources_from_args(args)
    parent = args.output_root.absolute()
    for path in (parent, TEMP):
        if not path.is_dir():
            parser.error("Output/temp parent must already exist")
        for p in (path, *path.parents):
            if p.is_symlink() or p.is_junction():
                parser.error("Reparse output/temp parent refused")
    verify(sources)
    test_zip_validator()
    if args.candidate:
        target = parent / ("ccpl-package-" + uuid.uuid4().hex)
    else:
        target = parent / NAME
    manifest = prepare(target, sources)
    print("PACKAGE=" + str(target), flush=True)
    # Windows PowerShell 5.1, no policy bypass. Fixture EXE is plain text and
    # is never started; the only spawned helper is PowerShell for mutex tests.
    subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(target / "instaluj.ps1"), "-SelfTest"], check=True)
    subprocess.run([sys.executable, "-B", str(HERE / "test_launchers.py"), str(target)], check=True)
    if args.candidate:
        return
    archive = target.parent / (NAME + ".zip")
    make_zip(target, archive)
    checks = audit_zip(target, archive)
    second = TEMP / ("ccpl-zip-repro-" + uuid.uuid4().hex + ".zip")
    make_zip(target, second)
    if digest(archive) != digest(second) or archive.stat().st_size != second.stat().st_size:
        raise ValueError("ZIP determinism failed")
    with archive.open("rb") as a, second.open("rb") as b:
        while True:
            x, y = a.read(1024 * 1024), b.read(1024 * 1024)
            assert x == y
            if not x:
                break
    result = dict(version=VERSION, archive=archive.name, sha256=digest(archive), bytes=archive.stat().st_size,
                  entries=checks, deterministicByteIdentical=True, timestamp="2026-09-28T00:00:00", payload=manifest["payload"],
                  status=manifest["status"], technicalReadiness=manifest["technicalReadiness"],
                  distributionPermission=manifest["distributionPermission"],
                  manifestSha256=digest(target / "release-manifest.json"),
                  sha256sumsSha256=digest(target / "SHA256SUMS.txt"))
    write_new(target.parent / (NAME + ".zip.sha256.txt"), text_bytes(f"{result['sha256']}  {archive.name}\nbytes: {result['bytes']}\n"))
    write_new(target.parent / (NAME + ".build-report.json"), text_bytes(json.dumps(result, indent=2) + "\n"))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
