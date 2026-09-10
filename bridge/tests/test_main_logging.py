"""The bot token must not reach the log.

httpx logs every request URL at INFO level, and the Telegram bot token is part of
the URL. At info level -- the level the README tells you to run at -- that writes
the token into the log file, and into any log pasted into a bug report.
"""

import logging

from handy_bridge.__main__ import main


def test_httpx_is_quieted_so_the_token_never_reaches_the_log(tmp_path, monkeypatch):
    logging.getLogger("httpx").setLevel(logging.NOTSET)

    # main() fails on the missing config before doing anything else; the logging
    # setup happens first, which is the part under test.
    assert main(["--config", str(tmp_path / "nao-existe.toml")]) == 2
    assert logging.getLogger("httpx").level == logging.WARNING


def test_an_info_run_would_not_emit_the_request_url(tmp_path):
    main(["--config", str(tmp_path / "nao-existe.toml"), "--log-level", "info"])
    # A URL de requisicao sai em INFO; em WARNING ela nao sai.
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
