"""Entrypoint: wire config, worker, mDNS and the HTTP server together."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from handy_bridge.config import ConfigError, load_config
from handy_bridge.discovery import advertise
from handy_bridge.postprocess import build as build_processor
from handy_bridge.server import create_app
from handy_bridge.telegram import TelegramSender
from handy_bridge.worker import NoteWorker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="handy-bridge")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = load_config(args.config)
        processor = build_processor(cfg.post_process)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    telegram = None
    if cfg.telegram.enabled:
        telegram = TelegramSender(cfg.telegram.token, cfg.telegram.chat_id)
        logging.getLogger(__name__).info("Telegram delivery enabled")

    worker = NoteWorker(cfg, processor, telegram=telegram)
    worker.start()

    zc = None
    try:
        zc, _ = advertise(cfg.port)
    except OSError:
        logging.getLogger(__name__).warning(
            "mDNS advertisement failed; the device will need a configured address"
        )

    app = create_app(cfg, submit=worker.submit)
    try:
        uvicorn.run(app, host="0.0.0.0", port=cfg.port, log_config=None)
    finally:
        if zc is not None:
            zc.close()
        worker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
