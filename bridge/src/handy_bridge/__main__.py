"""Entrypoint: wire config, worker, mDNS and the HTTP server together."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from handy_bridge.config import ConfigError, load_config
from handy_bridge.digest import DigestScheduler, DigestState
from handy_bridge.discovery import AddressWatcher
from handy_bridge.postprocess import build as build_processor
from handy_bridge.server import create_app
from handy_bridge.listener import TelegramListener
from handy_bridge.telegram import TelegramSender
from handy_bridge.threads import ThreadStore
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
    threads = None
    listener = None
    digest = None
    if cfg.telegram.enabled:
        telegram = TelegramSender(cfg.telegram.token, cfg.telegram.chat_id)
        threads = ThreadStore(cfg.audio_store / "threads.json")
        logging.getLogger(__name__).info("Telegram delivery enabled")
        if processor is not None:
            listener = TelegramListener(telegram, processor, threads, cfg.telegram.chat_id)
            listener.start()
            logging.getLogger(__name__).info("Telegram follow-up listener started")
        if cfg.digest.enabled:
            digest = DigestScheduler(cfg, telegram, DigestState(cfg.audio_store / "digest.json"))
            digest.start()
            logging.getLogger(__name__).info(
                "weekly digest scheduled for weekday %d at %02dh", cfg.digest.weekday, cfg.digest.hour)

    worker = NoteWorker(cfg, processor, telegram=telegram, threads=threads)
    worker.start()

    # Watches the address rather than announcing once: the laptop moves between
    # networks and the old announcement would send the device to the wrong router.
    announcer = AddressWatcher(cfg.port)
    announcer.start()

    app = create_app(cfg, submit=worker.submit)
    try:
        uvicorn.run(app, host="0.0.0.0", port=cfg.port, log_config=None)
    finally:
        if digest is not None:
            digest.stop()
        if listener is not None:
            listener.stop()
        announcer.stop()
        worker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
