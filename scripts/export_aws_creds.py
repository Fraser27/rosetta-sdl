# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3"]
# ///
"""Export AWS SSO credentials into a .env file (append or update in place).

Resolves credentials from the current AWS profile (SSO or otherwise) via boto3
and writes AWS_DEFAULT_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and
AWS_SESSION_TOKEN into the .env file. Existing entries for those keys are
replaced in place; every other line — including NEO4J_* credentials — is
preserved untouched. Re-run after each `aws sso login` to refresh the tokens.

Usage:
    uv run scripts/export_aws_creds.py [--profile NAME] [--region REGION]
                                       [--env-file PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# Written in this order when a key is not already present in the file.
MANAGED_ORDER = (
    "AWS_DEFAULT_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)


def resolve_credentials(profile: str | None, region: str | None) -> dict[str, str]:
    """Resolve credentials from the AWS profile/SSO cache into env-var form."""
    session = boto3.Session(profile_name=profile, region_name=region)

    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("no credentials found for the selected profile")
    frozen = credentials.get_frozen_credentials()

    resolved = {
        "AWS_ACCESS_KEY_ID": frozen.access_key,
        "AWS_SECRET_ACCESS_KEY": frozen.secret_key,
    }
    if frozen.token:
        resolved["AWS_SESSION_TOKEN"] = frozen.token
    if session.region_name:
        resolved["AWS_DEFAULT_REGION"] = session.region_name
    return resolved


def upsert_env(
    path: Path, updates: dict[str, str], dry_run: bool = False
) -> dict[str, str]:
    """Update matching KEY=value lines and append the rest, preserving all else.

    Returns a mapping of each written key to "updated" or "appended".
    """
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    outcome: dict[str, str] = {}

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        for key in list(remaining):
            if stripped.startswith(f"{key}="):
                lines[i] = f"{key}={remaining.pop(key)}"
                outcome[key] = "updated"
                break

    for key in MANAGED_ORDER:
        if key in remaining:
            lines.append(f"{key}={remaining.pop(key)}")
            outcome[key] = "appended"

    if not dry_run:
        path.write_text("\n".join(lines) + "\n")
    return outcome


def redact(key: str, value: str) -> str:
    """Show region in full, the access key's last 4 chars, and mask secrets."""
    if key == "AWS_DEFAULT_REGION":
        return value
    if key == "AWS_ACCESS_KEY_ID":
        return f"...{value[-4:]}" if len(value) > 4 else "***"
    return "***"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--region", default=None)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and report the changes without writing to the file.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        resolved = resolve_credentials(args.profile, args.region)
    except (BotoCoreError, ClientError, RuntimeError) as exc:
        print(f"error: could not resolve AWS credentials: {exc}", file=sys.stderr)
        login = "aws sso login" + (f" --profile {args.profile}" if args.profile else "")
        print(f"hint: run `{login}` first", file=sys.stderr)
        return 1

    if "AWS_DEFAULT_REGION" not in resolved:
        print(
            "error: no region configured; pass --region or set one in your profile",
            file=sys.stderr,
        )
        return 1

    path = Path(args.env_file)
    outcome = upsert_env(path, resolved, dry_run=args.dry_run)

    prefix = "[dry-run] would write" if args.dry_run else "wrote"
    for key in MANAGED_ORDER:
        if key in resolved:
            print(f"{prefix} {key}={redact(key, resolved[key])} ({outcome[key]})")
    if not args.dry_run:
        print(f"updated {path} (existing entries preserved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
