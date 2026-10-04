"""Audit the exact Git tracked/staged file set, without echoing secret values."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_NAMES = {".env", "notification_settings.json", "restart_state.json"}
FORBIDDEN_PARTS = {".venv", "data", "logs", "backup", "backups", "FutuOpenD", "__pycache__"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".exe", ".7z", ".lnk", ".pem", ".key", ".pfx", ".p12"}
RULES = {
    "github-token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "telegram-token": re.compile(r"\b[0-9]{7,}:[A-Za-z0-9_-]{25,}\b"),
    "feishu-user-id": re.compile(r"\bou_[a-f0-9]{20,}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "personal-windows-path": re.compile(r'[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b|Default\b)[^\s"\r\n]+', re.I),
    "personal-macos-path": re.compile(r"/Users/[A-Za-z0-9_.-]+/"),
    "bot-webhook-secret": re.compile(
        r'https://(?:open\.feishu\.cn|qyapi\.weixin\.qq\.com)/[^\s"\r\n]{10,}(?:hook|key=)[^\s"\r\n]+'
    ),
}


def scan_files(paths):
    issues = []
    for relative in paths:
        path = Path(relative)
        if (
            path.name in FORBIDDEN_NAMES
            or FORBIDDEN_PARTS.intersection(path.parts)
            or path.suffix.lower() in FORBIDDEN_SUFFIXES
            or ".db-" in path.name
            or ".sqlite3-" in path.name
        ):
            issues.append((relative, "runtime-or-private-file"))
            continue
        content = (ROOT / path).read_bytes()
        if b"\0" in content:
            issues.append((relative, "unexpected-binary-file"))
            continue
        text = content.decode("utf-8", errors="replace")
        for name, rule in RULES.items():
            if rule.search(text):
                issues.append((relative, name))
    return issues


def main():
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
    paths = result.stdout.decode().split("\0")[:-1]
    if not paths:
        print("No tracked files. Stage the intended public files first.", file=sys.stderr)
        return 1
    issues = scan_files(paths)
    for path, category in issues:
        print(f"{path}: {category}", file=sys.stderr)
    print(f"Privacy audit: {len(paths)} files, {len(issues)} findings; secret values were not printed.")
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
