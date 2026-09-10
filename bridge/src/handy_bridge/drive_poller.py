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
from uuid import uuid4

from handy_bridge import wav as wav_mod
from handy_bridge.config import Config
from handy_bridge.drive_inbox import plan_inbox
from handy_bridge.pipeline import IncomingNote
from handy_bridge.server import MIN_AUDIO_SECONDS, preserve_rejected, remember_delivered

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

    Duas threads escrevem aqui com a mesma instância -- a do poller e o event
    loop do uvicorn, que é onde o POST /v1/notes registra -- então toda
    mutação é serializada.
    """

    def __init__(self, path: Path):
        self._path = path
        # Cobre o read-modify-write inteiro, não só o set: duas escritas
        # simultâneas deixavam um dump curto por cima do prefixo de um mais
        # longo, e um drive-seen.json ilegível começa vazio no boot seguinte e
        # reprocessa tudo que ainda estiver na pasta.
        self._lock = threading.Lock()
        self._ids: set[str] = set()
        if path.exists():
            try:
                self._ids = set(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                log.warning("estado de ids do Drive ilegível em %s; começando vazio", path)

    def snapshot(self) -> set[str]:
        with self._lock:
            return set(self._ids)

    def add(self, note_id: str) -> None:
        with self._lock:
            self._ids.add(note_id)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Escrita atômica: o mesmo cuidado das notas, porque um arquivo de
            # estado truncado faria o bridge reprocessar tudo. O temporário tem
            # nome próprio a cada escrita -- um `.tmp` fixo era o ponto de
            # colisão em si: as duas threads escreviam o mesmo arquivo e o
            # perdedor do replace levantava FileNotFoundError (PermissionError
            # no Windows, com o handle da outra ainda aberto) depois de a nota
            # já ter sido entregue.
            temp = self._path.with_name(f"{self._path.name}.{uuid4().hex}.tmp")
            try:
                temp.write_text(json.dumps(sorted(self._ids)), encoding="utf-8")
                temp.replace(self._path)
            finally:
                # Um write que falhou no meio não deixa lixo na pasta de estado.
                temp.unlink(missing_ok=True)


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
            raw_sidecar = None
            if note.sidecar is not None:
                raw_sidecar = self._drive.download(note.sidecar.id)

            self._cfg.audio_store.mkdir(parents=True, exist_ok=True)
            target = self._cfg.audio_store / f"{note.note_id}.wav"
            # Em disco antes de qualquer validação, exatamente como no
            # POST /v1/notes: o áudio é a única parte que ninguém reconstrói, e
            # cada porteira abaixo é razão para guardá-lo, não para perdê-lo.
            target.write_bytes(self._drive.download(note.wav.id))

            # As mesmas três porteiras do POST /v1/notes, na mesma ordem. Sem
            # elas, um WAV inutilizável levantava no wav.inspect sem guarda de
            # pipeline.py, era engolido numa linha de log pelo worker, e a
            # cópia no Drive já tinha sido apagada -- gravação perdida em
            # silêncio.
            meta: dict = {}
            if raw_sidecar is not None:
                try:
                    meta = json.loads(raw_sidecar)
                except ValueError:
                    # Sidecar ilegível é recusa, como na rota da LAN. Um
                    # sidecar *ausente* continua tolerado (a nota entra sem
                    # âncora); ilegível significa que o firmware escreveu algo
                    # que ninguém sabe ler, e escrever a nota sem âncora
                    # esconderia isso.
                    self._refuse(note, target, "meta is not valid JSON",
                                 meta=raw_sidecar.decode("utf-8", errors="replace"))
                    continue

            try:
                info = wav_mod.inspect(target)
            except wav_mod.InvalidWav as exc:
                self._refuse(note, target, f"invalid WAV: {exc}")
                continue

            if info.duration_s < MIN_AUDIO_SECONDS:
                self._refuse(note, target, f"audio too short: {info.duration_s:.2f}s")
                continue

            # Marcado antes de entregar: se o processo morrer entre as duas
            # coisas, a nota é perdida uma vez. Marcar depois arriscaria
            # reprocessar e escrever a nota duas vezes, que é pior porque nada
            # no vault reconciliaria as duas. A chave é o note_id (o stem):
            # um retry do device sobe o mesmo arquivo com um file id novo, e
            # é o stem que identifica a gravação já entregue.
            self._state.add(note.note_id)
            self._submit(IncomingNote(note_id=note.note_id, wav_path=target, meta=meta))
            handed += 1
            log.info("nota %s recebida pelo Drive (%.1fs)", note.note_id, info.duration_s)
            self._purge(note)
        return handed

    def _refuse(self, note, target: Path, reason: str, meta: str | None = None) -> None:
        """Guarda o áudio, diz por quê, e tira a gravação do caminho.

        Marcada como processada e removida do Drive: uma recusa é definitiva,
        e sem isso a mesma gravação seria baixada e recusada a cada 30
        segundos para sempre.
        """
        preserve_rejected(target, note.note_id, reason, meta=meta)
        # Depois de o áudio estar guardado, e por isso registrado sem poder
        # levantar: a gravação já está segura, e uma falha de escrita aqui
        # abortaria o poll e deixaria no Drive um arquivo que voltaria a ser
        # recusado a cada 30 segundos. Diferente do add antes do _submit lá
        # em cima, que é fatal de propósito -- ali a nota ainda não foi
        # entregue, e entregar sem registrar é que escreveria a nota duas vezes.
        remember_delivered(self._state, note.note_id, reason)
        self._purge(note)

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
