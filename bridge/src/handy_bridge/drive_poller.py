"""A segunda porta de entrada: notas que chegaram pelo Drive.

Termina exatamente onde o POST /v1/notes termina -- montando um IncomingNote e
chamando submit(). Tudo depois disso é a pipeline que já existia, e é por isso
que uma nota que veio do Drive produz a mesma nota que uma que veio da LAN.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from handy_bridge.config import Config
from handy_bridge.drive_inbox import plan_inbox
from handy_bridge.pipeline import IncomingNote

log = logging.getLogger(__name__)


class ProcessedIds:
    """Note ids (o stem do arquivo) já entregues ao worker.

    A chave é o note_id, não o file id do Drive: um upload que falha no
    sidecar é refeito pelo device, e files.create cunha um id novo a cada
    tentativa sem impedir nomes repetidos. Duas tentativas da mesma gravação
    têm ids diferentes e o mesmo stem -- só o stem identifica a gravação.
    Marcar por file id deixaria o retry passar como nota nova.

    Esta lista existe porque remover do Drive pode falhar depois de a nota já
    ter sido processada, e notas são fonte: escritas uma vez, nunca
    reescritas. Sem esta lista, um delete falho viraria uma segunda nota no
    vault a cada poll.
    """

    def __init__(self, path: Path):
        self._path = path
        self._ids: set[str] = set()
        if path.exists():
            try:
                self._ids = set(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                log.warning("estado de ids do Drive ilegível em %s; começando vazio", path)

    def snapshot(self) -> set[str]:
        return set(self._ids)

    def add(self, note_id: str) -> None:
        self._ids.add(note_id)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Escrita atômica: o mesmo cuidado das notas, porque um arquivo de
        # estado truncado faria o bridge reprocessar tudo.
        temp = self._path.with_suffix(".tmp")
        temp.write_text(json.dumps(sorted(self._ids)), encoding="utf-8")
        temp.replace(self._path)


class DrivePoller:
    def __init__(
        self,
        cfg: Config,
        drive,
        submit: Callable[[IncomingNote], None],
        state: ProcessedIds,
        now: Callable[[], datetime] | None = None,
    ):
        self._cfg = cfg
        self._drive = drive
        self._submit = submit
        self._state = state
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> int:
        """Entrega ao worker tudo que está pronto. Devolve quantas notas foram."""
        plan = plan_inbox(self._drive.list_inbox(), self._state.snapshot(), self._now())

        # Primeiro o lixo, e antes de qualquer download: a pasta não se drena
        # sozinha, e uma listagem entupida é o que faz uma gravação nova ficar
        # invisível. Se um download abaixo falhar e abortar o poll, a limpeza
        # já aconteceu.
        for stale in plan.stale:
            self._forget(stale.id)

        handed = 0
        for note in plan.ready:
            meta = {}
            if note.sidecar is not None:
                try:
                    meta = json.loads(self._drive.download(note.sidecar.id).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    # Sidecar ilegível não custa a nota: ela entra sem âncora,
                    # como uma gravação sem sidecar no cartão.
                    log.warning("sidecar de %s ilegível; nota entra sem âncora", note.note_id)

            self._cfg.audio_store.mkdir(parents=True, exist_ok=True)
            target = self._cfg.audio_store / f"{note.note_id}.wav"
            target.write_bytes(self._drive.download(note.wav.id))

            # Marcado antes de entregar: se o processo morrer entre as duas
            # coisas, a nota é perdida uma vez. Marcar depois arriscaria
            # reprocessar e escrever a nota duas vezes, que é pior porque nada
            # no vault reconciliaria as duas. A chave é o note_id (o stem):
            # um retry do device sobe o mesmo arquivo com um file id novo, e
            # é o stem que identifica a gravação já entregue.
            self._state.add(note.note_id)
            self._submit(IncomingNote(note_id=note.note_id, wav_path=target, meta=meta))
            handed += 1
            log.info("nota %s recebida pelo Drive", note.note_id)
            self._purge(note)
        return handed

    def _purge(self, note) -> None:
        """Tira do Drive tudo que pertence a esta gravação."""
        remote_ids = [note.wav.id, *(f.id for f in note.extra_copies)]
        if note.sidecar is not None:
            remote_ids.append(note.sidecar.id)
        for remote_id in remote_ids:
            self._forget(remote_id)

    def _forget(self, remote_id: str) -> None:
        try:
            self._drive.delete(remote_id)
        except Exception as exc:  # noqa: BLE001 - remover é melhor-esforço
            log.warning("não removi %s do Drive: %s", remote_id, exc)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="drive-poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - um erro não mata o poller
                log.warning("poll do Drive falhou: %s", exc)
            self._stop.wait(self._cfg.drive.poll_s)
