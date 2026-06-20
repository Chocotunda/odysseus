#!/usr/bin/env python3
"""Surface security-relevant upstream commits since the last check.

Part of the B2 fork strategy (see docs/superpowers/specs/2026-06-20-hermes-
odysseus-hybrid-direction.md §7): we stay a tracking fork but merge on our own
cadence, so we need a cheap, filtered feed of the upstream fixes that actually
matter for a single-user localhost instance — security/auth/encryption/injection
fixes and dependency bumps — instead of reviewing ~1,400 commits/month by hand.

Run ad hoc, or on a schedule (see the cron/launchd hint printed at the end).
It fetches `upstream`, lists commits on upstream/dev since the last watermark
whose subject OR touched files look security-relevant, then advances the
watermark. Read-only w.r.t. your code; it never merges anything.

    python3 scripts/hub_upstream_security_scan.py            # scan + advance watermark
    python3 scripts/hub_upstream_security_scan.py --peek     # scan WITHOUT advancing
    python3 scripts/hub_upstream_security_scan.py --since <ref>
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

UPSTREAM = "upstream/dev"
# First-run fallback: the merge-base recorded when this strategy was adopted.
FALLBACK_BASE = "ed18192a8ebd235ce38826ee5428e53445ec2455"
WATERMARK = Path(__file__).resolve().parent.parent / ".git" / "hub_upstream_scan.sha"

# Subject/file keywords worth a human look on a single-user localhost box.
KEYWORDS = re.compile(
    r"\b(security|vuln|cve|rce|xss|csrf|ssrf|inject|sanitiz|escap|traversal|"
    r"auth|authz|authn|token|secret|credential|password|encrypt|fernet|harden|"
    r"owner|scope|isolation|leak|exfil|permission|chmod|0o600)\b",
    re.IGNORECASE,
)
# Dependencies that sit directly under credential handling — flag any bump.
DEP_FILES = re.compile(r"requirements.*\.txt|pyproject\.toml|(cryptography|caldav|nh3|mcp|bcrypt|pyotp)")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=WATERMARK.parent.parent).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--peek", action="store_true", help="scan without advancing the watermark")
    ap.add_argument("--since", help="override the starting ref (default: stored watermark, else merge-base)")
    args = ap.parse_args()

    if not git("remote").splitlines().__contains__("upstream"):
        print("✗ no 'upstream' remote configured.", file=sys.stderr)
        return 2

    print(f"… fetching {UPSTREAM.split('/')[0]} …")
    subprocess.run(["git", "fetch", "upstream", "-q"], cwd=WATERMARK.parent.parent)

    since = args.since or (WATERMARK.read_text().strip() if WATERMARK.exists() else FALLBACK_BASE)
    tip = git("rev-parse", UPSTREAM)
    if not tip:
        print(f"✗ cannot resolve {UPSTREAM}.", file=sys.stderr)
        return 2
    if since == tip:
        print(f"✓ up to date — nothing new on {UPSTREAM} since last scan.")
        return 0

    # One line per commit: <sha>\x1f<subject>\x1f<files...>
    rng = f"{since}..{UPSTREAM}"
    log = git("log", "--no-merges", "--name-only", "--format=%x1e%h%x1f%s%x1f", rng)
    total = 0
    hits = []
    for rec in log.split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        total += 1
        try:
            sha, subject, files = rec.split("\x1f", 2)
        except ValueError:
            continue
        files = files.strip()
        why = []
        if KEYWORDS.search(subject):
            why.append("subject")
        if DEP_FILES.search(files):
            why.append("dep/file")
        if why:
            hits.append((sha, subject.strip(), ",".join(why)))

    print(f"\nScanned {total} new upstream commit(s) in {rng}.")
    if not hits:
        print("✓ none look security- or dependency-relevant. (Cosmetic/feature churn — safe to ignore.)")
    else:
        print(f"⚠ {len(hits)} worth a look — cherry-pick the single-user-relevant ones:\n")
        for sha, subject, why in hits:
            print(f"  {sha}  [{why}]  {subject}")
        print("\n  Inspect:   git show <sha>")
        print("  Bring in:  git cherry-pick <sha>   (or note it and batch at your bi-weekly merge)")

    print("\n  Dependency CVEs are topology-independent — also run:  ./venv/bin/pip-audit  (pip install pip-audit)")

    if args.peek:
        print(f"\n(--peek: watermark NOT advanced; still at {since[:12]})")
    else:
        WATERMARK.write_text(tip + "\n")
        print(f"\nWatermark advanced to {tip[:12]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
