"""Run a single local state writer; avoid reloader/multiple worker corruption."""
import argparse
import fcntl
import os
from pathlib import Path

import uvicorn

from spotforge.api import create_app


def main():
    parser = argparse.ArgumentParser(description="SpotForge local studio")
    parser.add_argument("--port", type=int, default=4831)
    parser.add_argument("--data-dir", default=os.environ.get("SPOTFORGE_DATA_DIR", "spotforge-data"))
    parser.add_argument("--output-dir", default=os.environ.get("SPOTFORGE_OUTPUT_DIR"))
    args = parser.parse_args()
    root = Path(args.data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".server.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Ein anderer SpotForge-Prozess verwendet diesen Datenordner.")
        uvicorn.run(create_app(root, args.output_dir), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
