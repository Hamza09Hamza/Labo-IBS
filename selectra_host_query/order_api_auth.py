"""Persistent local credential for the analyzer order API."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def load_or_create_order_api_token(path: str | os.PathLike[str]) -> str:
    """Return the stable token at *path*, creating it securely when absent."""
    token_path = Path(path)
    token_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        token = token_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        try:
            file_descriptor = os.open(
                token_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            # Another startup process won the creation race. Its credential is
            # authoritative, so read it instead of replacing it.
            token = token_path.read_text(encoding="ascii").strip()
        else:
            with os.fdopen(file_descriptor, "w", encoding="ascii", newline="\n") as stream:
                stream.write(f"{token}\n")

    if len(token) < 32 or any(character.isspace() for character in token):
        raise RuntimeError(
            f"Order API token file is empty or invalid: {token_path}. "
            "Move the file aside and restart LaboBridge to generate a new token."
        )

    # Windows ignores this mode, but it protects the credential on platforms
    # that implement POSIX permissions.
    try:
        token_path.chmod(0o600)
    except OSError:
        pass

    return token
