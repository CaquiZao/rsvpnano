from datetime import datetime, timedelta, timezone

from handy_bridge.drive_inbox import RemoteFile, plan_inbox

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def at(minutes_ago: int) -> datetime:
    return NOW - timedelta(minutes=minutes_ago)


def test_a_pair_is_ready():
    files = [
        RemoteFile("w1", "20260910-120000.wav", at(1)),
        RemoteFile("s1", "20260910-120000.json", at(1)),
    ]
    ready = plan_inbox(files, processed_ids=set(), now=NOW).ready
    assert len(ready) == 1
    assert ready[0].note_id == "20260910-120000"
    assert ready[0].wav.id == "w1"
    assert ready[0].sidecar.id == "s1"


def test_a_wav_without_its_sidecar_waits_for_the_grace_period():
    files = [RemoteFile("w1", "20260910-120000.wav", at(1))]
    assert plan_inbox(files, processed_ids=set(), now=NOW).ready == []


def test_a_wav_whose_sidecar_never_came_is_ready_after_the_grace_period():
    # Sidecar perdido não custa a nota: ela sobe sem âncora.
    files = [RemoteFile("w1", "20260910-114000.wav", at(20))]
    ready = plan_inbox(files, processed_ids=set(), now=NOW).ready
    assert len(ready) == 1
    assert ready[0].sidecar is None


def test_an_already_processed_wav_is_not_offered_again():
    files = [
        RemoteFile("w1", "20260910-120000.wav", at(10)),
        RemoteFile("s1", "20260910-120000.json", at(10)),
    ]
    assert plan_inbox(files, processed_ids={"20260910-120000"}, now=NOW).ready == []


def test_a_reuploaded_wav_with_a_new_file_id_is_not_offered_again():
    # Regressão do bug real: o device reenvia depois de um Retry e o Drive
    # cunha um file id novo para o mesmo stem. Sob a regra antiga (dedup por
    # wav.id) isso passaria, porque "w2" nunca foi visto -- e produziria uma
    # segunda nota. A chave correta é o note_id (o stem), que já está marcado.
    files = [
        RemoteFile("w2", "20260910-120000.wav", at(1)),
        RemoteFile("s2", "20260910-120000.json", at(1)),
    ]
    assert plan_inbox(files, processed_ids={"20260910-120000"}, now=NOW).ready == []


def test_two_wavs_sharing_a_stem_collapse_into_one_ready_note():
    # Um upload que falhou no sidecar e foi refeito deixa dois .wav com o
    # mesmo stem e ids diferentes na pasta. Isso deve virar UMA nota, a
    # partir do wav mais antigo; o outro é reportado como cópia extra para
    # o chamador apagar -- não para virar uma segunda nota.
    files = [
        RemoteFile("w1", "20260910-120000.wav", at(10)),
        RemoteFile("w2", "20260910-120000.wav", at(1)),
        RemoteFile("s1", "20260910-120000.json", at(1)),
    ]
    ready = plan_inbox(files, processed_ids=set(), now=NOW).ready
    assert len(ready) == 1
    assert ready[0].wav.id == "w1"
    assert [f.id for f in ready[0].extra_copies] == ["w2"]


def test_a_sidecar_without_its_recording_is_never_a_note():
    files = [RemoteFile("s1", "20260910-120000.json", at(10))]
    assert plan_inbox(files, processed_ids=set(), now=NOW).ready == []


def test_the_oldest_recording_is_offered_first():
    files = [
        RemoteFile("w2", "20260910-120000.wav", at(1)),
        RemoteFile("s2", "20260910-120000.json", at(1)),
        RemoteFile("w1", "20260910-110000.wav", at(60)),
        RemoteFile("s1", "20260910-110000.json", at(60)),
    ]
    ready = plan_inbox(files, processed_ids=set(), now=NOW).ready
    assert [r.note_id for r in ready] == ["20260910-110000", "20260910-120000"]


def test_files_that_are_neither_wav_nor_sidecar_are_ignored():
    files = [RemoteFile("x", "leia-me.txt", at(10))]
    assert plan_inbox(files, processed_ids=set(), now=NOW).ready == []


def test_an_already_processed_pair_is_reported_for_deletion():
    # Antes eram só ignorados, e a pasta os guardava para sempre. Com ~200
    # itens desses a listagem satura e uma gravação nova deixa de aparecer,
    # enquanto o device já recebeu 2xx e apagou a única cópia que tinha.
    files = [
        RemoteFile("w1", "20260910-120000.wav", at(10)),
        RemoteFile("w2", "20260910-120000.wav", at(9)),
        RemoteFile("s1", "20260910-120000.json", at(10)),
    ]
    plan = plan_inbox(files, processed_ids={"20260910-120000"}, now=NOW)
    assert plan.ready == []
    assert sorted(f.id for f in plan.stale) == ["s1", "w1", "w2"]


def test_an_orphan_sidecar_is_reported_for_deletion_after_the_grace_period():
    # Um sidecar sem .wav nenhum não vira nota jamais: plan_inbox o pulava, e
    # pular é o que enche a pasta.
    files = [RemoteFile("s1", "20260910-114000.json", at(20))]
    plan = plan_inbox(files, processed_ids=set(), now=NOW)
    assert plan.ready == []
    assert [f.id for f in plan.stale] == ["s1"]


def test_an_orphan_sidecar_within_the_grace_period_is_left_alone():
    # Dentro do prazo ele pode ser a metade de um par cujo .wav ainda está
    # subindo; apagar aqui destruiria a âncora de uma nota que vai chegar.
    files = [RemoteFile("s1", "20260910-120000.json", at(1))]
    plan = plan_inbox(files, processed_ids=set(), now=NOW)
    assert plan.ready == []
    assert plan.stale == []


def test_a_hostile_file_name_cannot_escape_the_note_id():
    # O note_id sai do nome que o Drive devolve e termina interpolado num
    # caminho de arquivo. A rota da LAN se defende com Path(...).stem; aqui a
    # defesa tem que ser a mesma.
    files = [
        RemoteFile("w1", "../../../etc/passwd.wav", at(10)),
        RemoteFile("s1", "../../../etc/passwd.json", at(10)),
    ]
    plan = plan_inbox(files, processed_ids=set(), now=NOW)
    assert len(plan.ready) == 1
    assert plan.ready[0].note_id == "passwd"
    assert plan.ready[0].sidecar.id == "s1"
