# Envio de notas de voz por Google Drive, quando a LAN não alcança

**Data:** 2026-09-10
**Status:** aprovado, aguardando plano de implementação
**Fork:** `CaquiZao/rsvpnano` (upstream: `ionutdecebal/rsvpnano`)
**Hardware alvo:** Waveshare ESP32-S3-Touch-LCD-3.49 (env `waveshare_esp32s3_touch_lcd_349_rev2`)
**Conta Drive:** `kakamoussallli@gmail.com`

## 1. Problema

A entrega de notas de voz só funciona quando o device e o bridge estão na mesma rede **e** o
firewall do Windows permite entrada. As duas condições falham com facilidade e falham mal.

O que aconteceu em 2026-09-10 é o caso típico: o usuário mudou de cômodo, trocou o Wi-Fi do PC e
da placa, e o envio parou. O bridge estava de pé, tinha percebido a troca de IP e re-anunciado por
mDNS corretamente — mas o Windows classificou a rede nova como **Pública**, e nesse perfil o
firewall bloqueia conexões de entrada. Do ponto de vista do device, o bridge simplesmente não
existia, e a única informação que a tela deu foi "Bridge não está na rede".

Dois problemas distintos, então:

1. **Entrada é frágil.** O tráfego entra na máquina do usuário, e é exatamente isso que o Windows
   bloqueia em rede nova. Toda rede desconhecida exige liberação manual.
2. **A falha mentiu.** O sistema tinha todos os dados para dizer "estou aqui, mas você não me
   alcança" e disse "não estou na rede".

E um requisito novo, maior que os dois: o usuário quer a placa em **qualquer** rede e o computador
em **qualquer outra**, com o envio funcionando sozinho. Isso não é descoberta em LAN — é atravessar
dois NATs, onde nenhuma das duas pontas consegue receber conexão.

## 2. Requisito

Chegar em qualquer lugar, apertar enviar, e a nota aparecer no vault. Sem liberar firewall, sem
saber o que é perfil de rede, sem depender de as duas pontas estarem na mesma sub-rede.

## 3. Decisões tomadas

| Decisão | Alternativas recusadas | Por quê |
|---|---|---|
| Google Drive como transporte de queda | Relay próprio com payload cifrado; Dropbox; Telegram; VPN WireGuard | O usuário escolheu a própria nuvem. Sem infra nova para manter, sem servidor no ar, e o áudio fica numa conta que é dele. |
| API do Drive direto, sem cliente de sync | Observar pasta sincronizada | Escapa de placeholder na nuvem, hidratação e cópia de conflito — problemas que este projeto já teve com OneDrive. Não depende de cliente instalado. |
| LAN primeiro, Drive como queda | Só Drive; Drive primeiro | Só Drive quebraria o que hoje funciona: rede sem internet deixaria de sincronizar. A LAN continua sendo o caminho rápido e offline; ela só deixa de ser fatal. |
| Escopo `drive.file` | `drive` completo | Dá acesso apenas aos arquivos que o próprio app cria. O bridge nunca vê o resto do Drive do usuário. |

**O que esta decisão custa, dito claramente:** o README promete que "o áudio nunca sai da sua
máquina". Com o Drive no caminho, isso deixa de ser verdade para as notas que caem na rota de
queda — o WAV transita e repousa, legível, na conta Google do usuário. A promessa que sobra é mais
estreita: o áudio nunca vai para um serviço de transcrição ou LLM na nuvem; a transcrição continua
100% local. **O README precisa ser corrigido nesse ponto**, não deixado como está.

## 4. Arquitetura e fluxo

A fila no cartão não muda. O que muda é o flush.

```
grava → fila no SD (inalterado)
          ↓
    1. tenta LAN:  /config/bridge.txt → mDNS → POST /v1/notes      (inalterado)
          ↓ bridge NÃO encontrado na rede
    2. upload Drive:  rsvp-nano/inbox/<id>.wav  e depois  <id>.json
          ↓
    3. bridge faz poll da pasta, baixa o par completo
          ↓
    4. entrega ao MESMO worker: IncomingNote → worker.submit()
          ↓
    pipeline atual, intocada: transcrição, nota, kanban, telegram, resumos
```

O ganho de isolamento está no passo 4. O Drive é apenas uma **segunda porta de entrada** que
produz um `IncomingNote`; nada do que já funciona é reescrito, e as duas rotas convergem antes da
transcrição. O `POST /v1/notes` continua sendo a porta da LAN e não sabe que a outra existe.

### 4.1 Quando a queda dispara

A queda para o Drive acontece **somente** quando nenhum endpoint de bridge foi encontrado —
`configuredBridge()` não respondeu e `discoverBridge()` voltou vazio.

Nunca quando um endpoint foi encontrado e o envio falhou no meio. Nesse segundo caso o bridge pode
ter recebido a nota, e subir a mesma gravação pelo Drive criaria uma nota duplicada. Falha em
trânsito continua sendo `Retry`: a nota fica na fila para o próximo flush.

## 5. Autenticação e segredos

Um projeto no Google Cloud, client OAuth do tipo **Desktop app**, escopo `drive.file`.

O consentimento acontece **uma vez, no PC**: o bridge ganha um comando que abre o navegador, o
usuário aprova com a conta acima, e o resultado é um refresh token. Não há tela de código no
device.

O device recebe o mesmo refresh token num arquivo no cartão — `/config/drive.toml`, com
`client_id`, `client_secret`, `refresh_token` e o id da pasta de destino. O caminho de escrita é a
transferência USB que o projeto já tem, o mesmo precedente do `/config/bridge.txt`.

**TOML, não JSON** (esta seção dizia `drive.json`): todo config que este firmware *lê* é TOML,
lido com glaze; ele escreve JSON (o sidecar da nota), mas nunca leu JSON de configuração, e abrir
essa exceção custaria um segundo parser no device para nada.

O device troca refresh token por access token num `POST` simples a cada flush (sem criptografia
além do TLS) e usa o access token no upload.

### 5.1 A armadilha dos 7 dias

Se a tela de consentimento do Google ficar em modo **Testing**, o Google **expira o refresh token
em 7 dias**. O sistema funcionaria por uma semana e pararia sozinho, com o device dizendo apenas
que o Drive recusou.

O app **tem** que ir para **Published**. Isso não é detalhe de configuração: é a diferença entre
uma solução e uma bomba de tempo. Reautenticar toda semana foi considerado e recusado.

### 5.2 TLS

O device usa `NetworkClientSecure`, que já é dependência, com a **CA raiz do Google pinada**.
`setInsecure()` está descartado: sem validar o certificado, qualquer um na rede intercepta o
upload, o que anularia a razão de tirar o áudio da LAN.

## 6. Semântica de falha

Existem agora dois transportes, e seus 4xx significam coisas opostas. A regra "só entrega
confirmada apaga áudio" continua valendo, num lugar só.

| Resultado | O que significa | Ação na fila |
|---|---|---|
| Bridge recusou (4xx na LAN) | O bridge julgou **a nota** | `Park` — sai da fila, fica no cartão como `.parked` |
| Drive 2xx | A nota está durável no Drive do usuário | `Delete` |
| Drive 400 / 401 / 403 / 404 | Token, permissão ou pasta. **Nada** sobre a nota | `Keep` + erro legível ("Drive recusou: token ou pasta") |
| Drive 429 / 5xx | Transitório | `Keep` |
| Falha de rede em qualquer rota | Transitório | `Keep` |

**Qualquer 2xx, e não "2xx com file id"** (esta tabela dizia com file id): o device lê apenas a
linha de status da resposta e nunca o corpo — drenar o JSON do Drive custaria tempo de rádio por
um file id que nada no device usa. O que confirma a durabilidade é o status; o `Delete` depende
dele e de mais nada.

**400 e 404 ficam com o 401/403, não com os transitórios.** Um 404 é o `folder_id` errado ou
apagado e um 400 é uma requisição que o Drive não vai aceitar como está; mandados para o `Retry`
genérico, a tela dizia "sem internet" e o usuário ia procurar o problema no roteador. Nenhum dos
quatro estaciona a nota: o problema é configuração, que o usuário corrige, e a gravação continua
na fila.

Um 403 do Drive **não** estaciona a nota: estacionar diria que a gravação é ruim, quando o problema
é configuração. A nota fica na fila e sobe quando o token for corrigido.

### 6.1 A mensagem quando as duas rotas falham

O segundo problema da seção 1 — a falha que mentiu — não desaparece com a rota de queda. Ela
remove o caso comum, mas se **não houver internet e nem bridge na rede**, o device continuaria
dizendo "Bridge não está na rede", que segue sendo a informação errada.

Então o erro passa a nomear a rota que falhou e por quê, em vez de culpar a rede:

| Situação | O que a tela diz |
|---|---|
| Bridge não achado, Drive não configurado | "Bridge fora da rede; Drive não configurado" |
| Bridge não achado, sem internet | "Bridge fora da rede; sem internet" |
| Bridge não achado, Drive recusou o token | "Drive recusou: token ou pasta" |
| Bridge não achado, Drive configurado com a pasta errada | "Drive recusou: token ou pasta" |
| Bridge achado, envio caiu no meio | "Envio falhou" (inalterado) |

As duas linhas do meio dizem a mesma coisa de propósito: do device, um 401 e um 404 são o mesmo
tipo de problema — o config está errado e está no cartão —, e a tela não tem espaço para explicar
qual dos dois campos é. O que ela não pode fazer é culpar a rede.

São strings e um enum, não arquitetura, mas é o que separa "não sei o que houve" de "sei o que
fazer".

Isso estende `actionFor()` em `VoiceQueuePlan` — a função pura criada em
`fix(voice): estacionar a nota que o bridge recusou`. Ela passa a receber o resultado de qualquer
transporte, e continua sendo o único lugar onde se decide destruir áudio, sob teste de host.

## 7. Pares incompletos e deduplicação

**Ordem de upload.** O device sobe o `.wav` primeiro e o `.json` **por último**. O bridge só
considera pronto um `.wav` que já tenha `.json` correspondente — a mesma regra de pareamento que
`planFrom()` aplica no cartão.

**Sidecar perdido não custa a nota.** Um `.wav` sem sidecar por mais de **5 minutos** é processado
sem âncora, coerente com `test_a_recording_without_a_sidecar_is_still_pending`. O prazo existe só
para não pegar um par no meio do upload; o pior upload plausível de um WAV de dez minutos termina
bem dentro dele.

**Deduplicação.** Se o bridge processar a nota e a remoção no Drive falhar, o poll seguinte criaria
uma segunda nota — notas são fonte, escritas uma vez, nunca reescritas, então nada as reconciliaria.
O bridge guarda num arquivo de estado no `audio_store`, no mesmo padrão de `threads.json` e
`digest.json`, os **`note_id`** já processados — o stem do arquivo.

**A chave é o stem, não o file id do Drive** (esta seção dizia o contrário, e o contrário não
funciona): `files.create` cunha um id novo a cada chamada e o Drive não impede nomes repetidos,
então as duas tentativas de subir **uma** gravação — a primeira, que confirmou o `.wav` e falhou no
sidecar, e o retry — têm ids diferentes e o mesmo stem. Marcado por file id, o retry passaria como
gravação nova e viraria a segunda nota que este parágrafo existe para evitar. Só o stem identifica
a gravação.

E é o mesmo registro para as duas portas de entrada: o `POST /v1/notes` grava o `note_id` depois
de entregar a nota ao worker. Sem isso, a gravação cuja cópia ficou no Drive (WAV confirmado,
sidecar falhou) entrava pela LAN no flush seguinte e voltava pelo Drive depois da carência — duas
notas, e `_unique_path` faz da segunda um arquivo novo, não uma sobrescrita.

**A pasta não se drena sozinha.** A remoção é melhor-esforço, o retry deixa cópia órfã, e um
sidecar sem `.wav` nunca vira nota. Por isso a listagem é paginada (`nextPageToken` seguido até o
fim) e `plan_inbox` também reporta o que deve ser apagado: os arquivos de um `note_id` já
processado e o sidecar órfão passada a carência. Sem as duas coisas, uma página de itens velhos
esconde as gravações novas — e o device já apagou a dele.

## 8. Lado do device

- **`src/voice/DriveRequest.{h,cpp}`** (novo, puro): monta a requisição de refresh e a de upload,
  e interpreta a resposta. Sem rede e sem Arduino, para caber em teste de host — mesmo desenho do
  `VoiceUploadBody`, que já é testado assim.
- **`src/voice/DriveUploader.{h,cpp}`** (novo): faz o I/O — TLS, refresh, upload do WAV e do
  sidecar, e devolve um resultado que o `actionFor()` traduz.
- **`src/voice/VoiceService.cpp`**: ganha o galho de queda, disparado só pela condição de 4.1.
- **`src/voice/VoiceQueuePlan.{h,cpp}`**: `actionFor()` estendido para os resultados do Drive.

## 9. Lado do bridge

- **`src/handy_bridge/drive.py`** (novo): refresh de token, listar pasta, baixar, remover. Um
  transporte injetável, para os testes rodarem sem rede.
- **`src/handy_bridge/drive_poller.py`** (novo): thread que faz poll a cada **30 s** por padrão,
  seleciona pares completos, deduplica por `note_id` (ver 7), aplica as mesmas três validações do
  `POST /v1/notes` — meta parseável, `wav.inspect`, duração mínima —, apaga o que o plano reporta
  como lixo e chama `worker.submit()`. Trinta segundos
  porque a rota de queda já é a lenta: o gargalo é o upload do device, não a espera do poll, e um
  intervalo curto multiplicaria chamadas de API sem encurtar nada perceptível.
- **`src/handy_bridge/config.py`**: seção `[drive]` — credenciais, id da pasta, intervalo
  (`poll_s`, padrão 30), interruptor.
- **`src/handy_bridge/__main__.py`**: sobe o poller quando `[drive]` está configurado, no mesmo
  formato do `TelegramListener` e do `DigestScheduler`.
- **`bridge/README.md`**: seção nova, e **correção da promessa de privacidade** citada em 3.

## 10. Testes

Tudo offline, sem hardware, sem rede e sem gastar tokens — como o resto da suíte.

**Bridge:** `drive.py` com transporte dublê (refresh, list, download, delete, a paginação e os
erros 401/429); o poller pegando só par completo, deduplicando por `note_id`, e tratando remoção
que falha sem criar nota dupla; e um teste de que nota vinda do Drive percorre a pipeline atual com
os dublês existentes, produzindo a mesma nota que o `POST /v1/notes` produziria — comparando as
duas notas, não apenas verificando que uma chegou, que é o que teria pego a divergência de
validação entre as duas portas.

**Device:** `actionFor()` para cada resultado de Drive da tabela em 6; e o `DriveRequest` montando
refresh e upload, com resposta de erro e de sucesso.

## 11. Riscos conhecidos

- **Consentimento em Testing expira em 7 dias.** Mitigação: publicar o app. Ver 5.1.
- **Banda e bateria.** Uma nota de 20 s são ~650 KB; dez minutos são ~19 MB. A rota de queda gasta
  rádio por mais tempo que a LAN. A rota rápida continua sendo a primeira tentada.
- **A promessa do README fica mais estreita.** Ver 3. Corrigir o texto é parte do trabalho, não
  nota de rodapé.
- **Credencial no cartão.** O refresh token fica legível em `/config/drive.toml` no SD. Quem tem o
  cartão tem acesso de escrita à pasta do app no Drive — não ao resto, pelo escopo `drive.file`.

## 12. Fora de escopo

- Guardar **várias** redes Wi-Fi no device. Continua uma credencial por vez; trocar de rede segue
  sendo digitar a senha na placa.
- Cifrar o payload no Drive. Foi oferecido e recusado em favor da simplicidade da própria nuvem.
- Buscar notas *do* bridge *para* o device (inversão de direção em LAN). Foi desenhado e
  abandonado quando o requisito virou "redes diferentes", que ela não resolve.
- Diagnóstico de firewall no bridge. Deixa de ser necessário: a queda para o Drive torna a
  liberação manual opcional em vez de obrigatória.
