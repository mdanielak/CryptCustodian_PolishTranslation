"""One-time, explicitly requested same-version correction; preserve old artifacts.

Only the exact previous release is accepted. No deletion, replacement or merging.
After this succeeds, the ordinary --build path still uses no-clobber creation.
"""
import json

from build_release import PROJECT, audit_directory, audit_zip, text_bytes, write_new
from verify_sources import NAME, digest


def main():
    parent = PROJECT / "release"
    package = parent / NAME
    archive = parent / (NAME + ".zip")
    report = parent / (NAME + ".build-report.json")
    sums = parent / (NAME + ".zip.sha256.txt")
    historical = parent / "superseded-595662b867d5"
    expected = "595662b867d5c9b0d7541348158f9bc6ef2ee2dc24c1de51a9b7aebc361600c2"
    if historical.exists():
        raise FileExistsError(historical)
    for path in (package, archive, report, sums):
        for p in (path, *path.parents):
            if p.is_symlink() or p.is_junction():
                raise ValueError("Reparse path refused: " + str(p))
    data = json.loads(report.read_text(encoding="utf-8"))
    if (digest(archive) != expected or archive.stat().st_size != 154759758
            or data["sha256"] != expected or data["bytes"] != 154759758
            or data["version"] != "1.0-beta" or data["archive"] != archive.name):
        raise ValueError("Not the pinned previous release")
    if sums.read_text(encoding="ascii").splitlines() != [
        f"{expected}  {archive.name}", "bytes: 154759758"
    ]:
        raise ValueError("Previous external SHA list mismatch")
    audit_directory(package)
    if audit_zip(package, archive) != data["entries"]:
        raise ValueError("Previous report/ZIP/package mismatch")
    historical.mkdir(exist_ok=False)
    write_new(historical / "SUPERSEDED.txt", text_bytes(
        "Historyczne artefakty 1.0-beta, zastępowane sekwencyjną korektą tej samej wersji.\n"
        "Nie są bieżącą paczką. Raport i wszystkie poprzednie bajty zachowano bez zmian.\n"
        "Historyczna skrócona nota zgody nie opisuje bieżących warunków wydania.\n"
        "Bieżące LICENSE-NOTICE.txt zawiera pełny cytat przekazany przez użytkownika.\n"
        f"Poprzedni ZIP SHA-256: {expected}\n", bom=True))
    # Windows rename refuses an existing destination. No recursive deletion.
    # If interrupted, stop and inspect; do not automatically retry or erase history.
    for path in (package, archive, report, sums):
        path.rename(historical / path.name)
    print("SUPERSEDED=" + str(historical))


if __name__ == "__main__":
    main()
