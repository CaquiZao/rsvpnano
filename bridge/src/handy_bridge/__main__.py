"""Entrypoint: wire config, worker, mDNS and the HTTP server together."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from pathlib import Path

import uvicorn

from handy_bridge.config import ConfigError, load_config
from handy_bridge.digest import DigestScheduler, DigestState
from handy_bridge.discovery import AddressWatcher
from handy_bridge.drive import Drive
from handy_bridge.drive_poller import DrivePoller, ProcessedIds
from handy_bridge.postprocess import build as build_processor
from handy_bridge.server import create_app
from handy_bridge.listener import TelegramListener
from handy_bridge.telegram import TelegramSender
from handy_bridge.threads import ThreadStore
from handy_bridge.worker import NoteWorker


def port_in_use(port: int) -> bool:
    """Whether something already holds the port we are about to serve on.

    A best-effort check, not a lock: it can only be racy, and that is fine,
    because its job is the error message rather than the binding.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0", port))
    except OSError:
        return True
    else:
        return False
    finally:
        probe.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="handy-bridge")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request URL at INFO, and the Telegram bot token is part of
    # the URL — so at info level the token ends up in the log file, and in any log
    # the user pastes somewhere. Warnings still come through, which is what
    # matters for diagnosing a failed call.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # The pid, because "which of these processes do I kill" is a question this
    # service has made people ask, and the answer was not in the log.
    logging.getLogger(__name__).info("handy-bridge starting, pid %d", os.getpid())

    try:
        cfg = load_config(args.config)
        processor = build_processor(cfg.post_process)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    # Checked before anything starts, because the alternative is what actually
    # happened: mDNS announces, the Telegram listener starts polling, and only
    # then uvicorn fails to bind with a traceback that says nothing about the
    # other bridge already running. Two listeners on one bot token also make
    # Telegram answer 409 for as long as the overlap lasts.
    if port_in_use(cfg.port):
        print(
            f"port {cfg.port} is already in use — another bridge is probably "
            f"running. Its log says 'handy-bridge starting, pid N'; stop that "
            f"pid and its parent, then start again.",
            file=sys.stderr,
        )
        return 3

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

    drive_poller = None
    # One store for both entrances. A recording can arrive twice -- the Drive
    # upload confirms the WAV, fails on the sidecar, and the next flush finds
    # the bridge on the LAN -- and the note_id POST /v1/notes records here is
    # what stops the copy left on Drive from becoming a second note in the
    # vault. Only the Drive route ever reads it, so it is only built when the
    # fallback is on; with it off, create_app keeps nothing.
    drive_seen = ProcessedIds(cfg.audio_store / "drive-seen.json") if cfg.drive.enabled else None
    if cfg.drive.enabled:
        drive_poller = DrivePoller(cfg, Drive(cfg.drive), worker.submit, drive_seen)
        drive_poller.start()
        logging.getLogger(__name__).info(
            "Drive fallback enabled, polling every %ds", cfg.drive.poll_s
        )

    # Watches the address rather than announcing once: the laptop moves between
    # networks and the old announcement would send the device to the wrong router.
    announcer = AddressWatcher(cfg.port)
    announcer.start()

    app = create_app(cfg, submit=worker.submit, processed=drive_seen)
    try:
        uvicorn.run(app, host="0.0.0.0", port=cfg.port, log_config=None)
    finally:
        if digest is not None:
            digest.stop()
        if listener is not None:
            listener.stop()
        announcer.stop()
        if drive_poller is not None:
            drive_poller.stop()
        worker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
