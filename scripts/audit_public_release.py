#!/usr/bin/env python3
"""Fail a public release when tracked text exposes personal or secret material."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache"}
RULES = {
    "macos-home-path": re.compile(r"/Users/(?!example(?:/|\b))[^/\s]+/"),  # public-audit: allow
    "email-address": re.compile(
        r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@"
        r"(?!(?:example\.(?:com|org|net)|users\.noreply\.github\.com)\b)"
        r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    ),
    "credential-url": re.compile(
        r"\b(?:postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?)://"
        r"[^\s:/]+:[^\s/@]+@",
        re.IGNORECASE,
    ),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "openai-token": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}
WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]{2,}\b")
PERSONAL_WORD_HASHES = {
    "bd576611e860bf15883a37b8d628c77e6dfb519757793a9d759fd861da067f77"
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str


def _text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        data = path.read_bytes()
        if b"\x00" in data:
            continue
        try:
            yield path, data.decode("utf-8")
        except UnicodeDecodeError:
            continue


def scan_tree(root: Path) -> list[Finding]:
    root = Path(root).resolve()
    findings = []
    for path, text in _text_files(root):
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(text.splitlines(), start=1):
            if "public-audit: allow" in line:
                continue
            if any(
                hashlib.sha256(word.lower().encode("utf-8")).hexdigest()
                in PERSONAL_WORD_HASHES
                for word in WORD_RE.findall(line)
            ):
                findings.append(Finding(relative, line_number, "personal-name"))
            for rule, pattern in RULES.items():
                if pattern.search(line):
                    findings.append(Finding(relative, line_number, rule))
    return findings


def scan_commit_identities(root: Path) -> list[Finding]:
    result = subprocess.run(
        ["git", "log", "--format=%an%x00%ae"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    findings = []
    for line_number, identity in enumerate(result.stdout.splitlines(), start=1):
        if any(
            hashlib.sha256(word.lower().encode("utf-8")).hexdigest()
            in PERSONAL_WORD_HASHES
            for word in WORD_RE.findall(identity)
        ):
            findings.append(Finding(".git/history", line_number, "personal-name"))
        if RULES["email-address"].search(identity):
            findings.append(Finding(".git/history", line_number, "email-address"))
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument(
        "--history", action="store_true", help="also audit Git author identities"
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    findings = scan_tree(root)
    if args.history:
        findings.extend(scan_commit_identities(root))
    if findings:
        for finding in findings:
            print(f"{finding.path}:{finding.line}: {finding.rule}")
        print(f"Public-release audit failed with {len(findings)} finding(s).")
        return 1
    print("Public-release audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
