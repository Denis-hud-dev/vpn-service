"""Fail CI if tracked files contain obvious private material; never print contents."""

from pathlib import Path, PurePosixPath
import re
import subprocess


PRIVATE_KEY = re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
TELEGRAM_TOKEN = re.compile(rb"\b[0-9]{6,12}:[A-Za-z0-9_-]{25,}\b")
VLESS_LINK = re.compile(rb"vless://[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}")
FORBIDDEN_SUFFIXES = {".dump", ".backup", ".pgdump", ".sql", ".key", ".pem", ".p12", ".pfx"}
FORBIDDEN_DIRS = {"backups", "node-private", "KudaVPN-private"}


def check_file(name: str) -> list[str]:
    path = PurePosixPath(name)
    parts = path.parts
    issues = []
    if any(part in FORBIDDEN_DIRS for part in parts[:-1]):
        issues.append("private directory")
    if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
        issues.append("environment file")
    if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.endswith((".sql.gz", ".sql.bz2", ".sql.xz")):
        issues.append("secret or database file")
    data = Path(name).read_bytes()
    for label, pattern in (("private key", PRIVATE_KEY), ("Telegram token", TELEGRAM_TOKEN), ("VLESS link", VLESS_LINK)):
        if pattern.search(data):
            issues.append(label)
    return issues


def main() -> int:
    names = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    failures = [(name, check_file(name)) for name in names if name and Path(name).is_file()]
    failures = [(name, issues) for name, issues in failures if issues]
    for name, issues in failures:
        print(f"Blocked tracked file {name}: {', '.join(issues)}")
    if failures:
        return 1
    print("Tracked-file hygiene: passed (heuristic check)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
