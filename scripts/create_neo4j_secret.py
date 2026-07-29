# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3", "python-dotenv"]
# ///
"""Seed the Neo4j (Aura) credentials into AWS Secrets Manager from a .env file.

Idempotent: creates the secret if it does not exist, otherwise updates its
value. Safe to re-run whenever the Aura password rotates.

Usage:
    uv run scripts/create_neo4j_secret.py [--secret-name NAME] [--region REGION]
                                          [--env-file PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

DEFAULT_SECRET_NAME = "rosetta-sdl/neo4j"
REQUIRED_KEYS = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")


def read_credentials(env_file: str) -> dict[str, str]:
    """Load and validate Neo4j credentials from the environment / .env file."""
    load_dotenv(env_file)

    missing = [key for key in REQUIRED_KEYS if not os.environ.get(key)]
    if missing:
        raise ValueError(f"Missing required variables: {', '.join(missing)}")

    uri = os.environ["NEO4J_URI"]
    if not uri.startswith("neo4j+s://"):
        raise ValueError(
            f"NEO4J_URI must be an Aura endpoint (neo4j+s://...), got: {uri}"
        )

    return {
        "uri": uri,
        "user": os.environ["NEO4J_USER"],
        "password": os.environ["NEO4J_PASSWORD"],
    }


def upsert_secret(
    client, secret_name: str, secret_string: str
) -> str:
    """Create the secret, or update its value if it already exists."""
    try:
        client.describe_secret(SecretId=secret_name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        client.create_secret(Name=secret_name, SecretString=secret_string)
        return "created"

    client.put_secret_value(SecretId=secret_name, SecretString=secret_string)
    return "updated"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-name", default=DEFAULT_SECRET_NAME)
    parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION"))
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the .env and print the secret shape without writing to AWS.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        creds = read_credentials(args.env_file)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    secret_string = json.dumps(creds)

    if args.dry_run:
        redacted = {**creds, "password": "***"}
        print(f"[dry-run] would write secret '{args.secret_name}': {redacted}")
        return 0

    try:
        client = boto3.client("secretsmanager", region_name=args.region)
        action = upsert_secret(client, args.secret_name, secret_string)
    except (BotoCoreError, ClientError) as exc:
        print(f"error: AWS request failed: {exc}", file=sys.stderr)
        return 1

    print(f"{action} secret '{args.secret_name}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
