"""
Checks that image archives written by `docker save` contain every layer their
manifests reference.

    python3 check-images.py images/*.tar

With Docker's containerd image store, `docker save` can write an archive
without some layers (when they are shared with other images on the machine,
only their unpacked copy exists) and still report success. `docker load` of
such a file fails only later, on the target server. Exit status 1 if any
archive is incomplete.
"""

import json
import sys
import tarfile
from pathlib import Path


def missing_blobs(path: Path) -> list[str]:
    missing = []
    with tarfile.open(path) as tar:
        names = set(tar.getnames())

        def read(name):
            return json.load(tar.extractfile(name))

        if "manifest.json" not in names:
            return ["manifest.json"]

        # The docker-archive view, used by `docker load`.
        for entry in read("manifest.json"):
            for blob in [entry["Config"], *entry["Layers"]]:
                if blob not in names:
                    missing.append(blob)

        # The OCI view, through nested indexes. Other platforms and build
        # attestations are left out of the archive on purpose.
        def walk(descriptor):
            blob = "blobs/" + descriptor["digest"].replace(":", "/")
            if blob not in names:
                arch = descriptor.get("platform", {}).get("architecture")
                attestation = descriptor.get("annotations", {}).get(
                    "vnd.docker.reference.type",
                )
                if arch in (None, "amd64") and not attestation:
                    missing.append(blob)
                return
            media = descriptor.get("mediaType", "")
            if "index" in media or "manifest.list" in media:
                for child in read(blob)["manifests"]:
                    walk(child)
            elif "manifest" in media:
                doc = read(blob)
                for child in [doc["config"], *doc["layers"]]:
                    child_blob = "blobs/" + child["digest"].replace(":", "/")
                    if child_blob not in names:
                        missing.append(child_blob)

        if "index.json" in names:
            for descriptor in read("index.json")["manifests"]:
                walk(descriptor)

    return missing


def main(paths: list[str]) -> int:
    failed = False
    for name in paths:
        path = Path(name)
        try:
            missing = missing_blobs(path)
        except (tarfile.TarError, OSError, KeyError, ValueError) as e:
            missing = [f"unreadable: {e}"]
        size = path.stat().st_size / 1024**3 if path.exists() else 0
        if missing:
            failed = True
            print(f"INCOMPLETE {path.name} ({size:.2f} GB): {len(missing)} missing")
        else:
            print(f"OK         {path.name} ({size:.2f} GB)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
