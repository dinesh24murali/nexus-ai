"""Push changed prompts from backend/prompts to Langfuse (label `production`). ADR-0008.

Run from the repo root in CI on merge to main:

    cd backend && uv run python ../scripts/sync_prompts.py [--dry-run]

Needs LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST (the environment of the target
Langfuse project). Exits 1 when a prompt changed without a version bump.
"""

import argparse
import sys

from app.core.config import get_settings
from app.llm.prompt_sync import PromptSyncError, sync_prompts
from app.llm.prompts import all_prompts
from langfuse import Langfuse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--dry-run", action="store_true", help="report what would change")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.langfuse_configured:
        print("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are not set", file=sys.stderr)
        return 2
    client = Langfuse(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        base_url=settings.langfuse_host,
        tracing_enabled=False,
    )
    try:
        results = sync_prompts(client, all_prompts(), dry_run=args.dry_run)
    except PromptSyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for r in results:
        print(f"{r.action.value:>9}  {r.name} (v{r.version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
