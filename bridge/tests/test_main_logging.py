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


# --- porta ocupada: mensagem acionavel em vez de traceback -------------------


def test_port_in_use_is_false_for_a_free_port():
    from handy_bridge.__main__ import port_in_use

    # 0 pede uma porta livre ao sistema; qualquer porta alta serve para o teste.
    import socket

    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    free = s.getsockname()[1]
    s.close()
    assert port_in_use(free) is False


def test_port_in_use_is_true_while_something_holds_it():
    import socket

    from handy_bridge.__main__ import port_in_use

    holder = socket.socket()
    holder.bind(("0.0.0.0", 0))
    holder.listen(1)
    taken = holder.getsockname()[1]
    try:
        assert port_in_use(taken) is True
    finally:
        holder.close()


def test_the_probe_does_not_keep_the_port(tmp_path):
    import socket

    from handy_bridge.__main__ import port_in_use

    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    free = s.getsockname()[1]
    s.close()

    # Sondar duas vezes tem de dar livre nas duas: a sonda nao pode virar o dono.
    assert port_in_use(free) is False
    assert port_in_use(free) is False


def test_main_refuses_to_start_when_the_port_is_taken(tmp_path, capsys):
    import socket

    holder = socket.socket()
    holder.bind(("0.0.0.0", 0))
    holder.listen(1)
    taken = holder.getsockname()[1]

    (tmp_path / "vault").mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'vault_path = "{(tmp_path / "vault").as_posix()}"\n'
        f'audio_store = "{(tmp_path / "audio").as_posix()}"\n'
        f"port = {taken}\n"
        '[asr]\nhandy_exe = "x"\nmodel = "m"\ntimeout_s = 1\n'
        '[post_process]\nenabled = false\nbackend = "none"\nmodel = ""\n',
        encoding="utf-8",
    )
    try:
        assert main(["--config", str(cfg)]) == 3
    finally:
        holder.close()
    # A mensagem tem de dizer o que fazer, nao so que falhou.
    err = capsys.readouterr().err
    assert "already in use" in err
    assert "pid" in err
