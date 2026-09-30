"""Read-only release input verification. Never use installed files as payload."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

VERSION = "1.0-beta-rc1-C-Force"
NAME = "CryptCustodian_Spolszczenie_v1.0-beta-rc1-C-Force"
TEMP = Path(os.environ.get("CC_RELEASE_TEMP", tempfile.gettempdir())).absolute()
ORIGINAL = {
    "data.win": "15e2c8ef57f4c5f599589b5d15021281a11b73757be54d0bbf739d57dd280b99",
    "translations.ini": "09d193cf8ef131c4bf9127bab49aeb9bf4c5cea88113a32d2c88813bd0dd286f",
}
PATCHED = {
    "data.win": "6ec2a376367df0832aea144f48e5b8928bc3e3cb466615bb97b2bdf51c477f1b",
    "translations.ini": "deeda976d7bf414bce0fe1af7d0f9719efa990bb83d4c956f989dfc886dc055f",
}
EXE = "252277ea55574e67877fe20fd4532ff97b6bc75dc514605593c6788b1fa9c0a0"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def input_arguments(parser):
    for name in ("data", "translations"):
        default = os.environ.get("CC_RELEASE_" + name.upper())
        parser.add_argument("--" + name, type=Path, default=default, required=not default,
                            help="Pinned offline payload; do not use installed game files.")


def sources_from_args(args):
    return {"data.win": args.data.absolute(), "translations.ini": args.translations.absolute()}


def private_path(text):
    # Generic checks also cover escaped JSON paths, without publishing local identities.
    return bool(re.search(
        r"(?i)[a-z]:[\\/]+(?:users[\\/]|steamlibrary[\\/])|"
        r"temp[\\/]+opencode|cc-(?:data|font|translations)-[a-z0-9-]*[0-9]{3}", text))


def verify(sources):
    if set(sources) != set(PATCHED):
        raise ValueError("Expected exactly data.win and translations.ini")
    result = {}
    for name, path in sources.items():
        for p in [path, *path.parents]:
            if p.is_symlink() or p.is_junction():
                raise ValueError("Reparse input: " + str(p))
        actual = digest(path)
        if actual != PATCHED[name]:
            raise ValueError("Payload SHA mismatch: " + name)
        result[name] = {"sha256": actual, "bytes": path.stat().st_size}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    input_arguments(parser)
    print(json.dumps(verify(sources_from_args(parser.parse_args())), indent=2))
