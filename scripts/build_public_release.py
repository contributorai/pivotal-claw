#!/usr/bin/env python3
"""Build the clean-history public release tree from an explicit allowlist."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def _patterns(manifest: Path):
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        candidate = Path(pattern)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"unsafe manifest entry: {pattern}")
        yield pattern


def build(root: Path, manifest: Path, destination: Path) -> list[str]:
    root = root.resolve()
    manifest = manifest.resolve()
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    selected = set()
    for pattern in _patterns(manifest):
        matches = [path for path in root.glob(pattern) if path.is_file()]
        if not matches:
            raise ValueError(f"manifest entry matched no files: {pattern}")
        for source in matches:
            resolved = source.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError(f"manifest entry escapes source tree: {pattern}")
            selected.add(source.relative_to(root))

    copied = []
    for relative in sorted(selected):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
        copied.append(relative.as_posix())
    return copied


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination")
    parser.add_argument("--root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", default="PUBLIC_RELEASE_FILES.txt")
    args = parser.parse_args(argv)
    root = Path(args.root)
    manifest = Path(args.manifest)
    if not manifest.is_absolute():
        manifest = root / manifest
    copied = build(root, manifest, Path(args.destination))
    print(f"Built public release with {len(copied)} files at {Path(args.destination).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
