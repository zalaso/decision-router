"""Explicit public-artifact download. Never loads remote Python or requests private tokens."""

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download

from decision_router.config import load_settings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/local.yaml"))
    args = parser.parse_args()
    settings = load_settings(args.config)
    path = snapshot_download(
        repo_id=settings.local_model,
        revision=settings.local_revision,
        token=False,
        allow_patterns=["*.json", "*.safetensors", "*.model", "vocab.txt", "README.md", "LICENSE*"],
    )
    print(path)
