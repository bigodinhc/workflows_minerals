# Design: formato do canal otimizado pra cópia manual no WhatsApp

- **Data:** 2026-07-30
- **Status:** Spec — aguardando revisão do usuário
- **Autor:** brainstorming colaborativo (usuário + Claude)
- **Contexto:** continuação de `2026-07-09-crons-canal-estetica-v2-design.md` (PR #4, merged) e `2026-07-09-telegram-channel-delivery-design.md` (PR #3, merged)

## 1. Problema

O canal privado do Telegram (`-1004478765840`, "Minerals Trading", 4 membros em piloto)
recebe hoje 5 workflows de cliente. Mas o usuário **copia essas mensagens do canal e
reenvia manualmente por WhatsApp** — e nesse trajeto a formatação morre.

Causa: `to_telegram_html()` (`webhook/bot/channel_delivery.py:61`) converte os marcadores
WhatsApp do texto-fonte (`*negrito*`, `` `mono` ``, `> citação`) em tags HTML do Telegram.
O Telegram então **renderiza** essas tags — os marcadores viram estilo e desaparecem do
texto. Ao copiar, vem o texto renderizado; ao colar no WhatsApp, chega tudo plano.

Problema secundário, levantado pelo usuário ao ver o smoke: as linhas de cotação usam
prefixo `> `, que vira `<blockquote>` — um painel cinza. O preço em `` ` ` `` (também
cinza) fica afogado nesse painel. Cinza sobre cinza, o destaque numérico some.

## 2. Restrições verificadas (não assumidas)

### 2.1 Vocabulário do WhatsApp (FAQ oficial, `faq.whatsapp.com/539178204879377`)

| Estilo | Sintaxe | Escopo |
|---|---|---|
| Negrito | `*texto*` | inline |
| Itálico | `_texto_` | inline |
| Riscado | `~texto~` | inline |
| Inline code | `` `texto` `` | inline — mono com fundo cinza |
| Monospace | ` ```texto``` ` | bloco |
| Lista | `- item` / `* item` | linha |
| Lista numerada | `1. item` | linha |
| Citação | `> texto` | **linha inteira**, repetida por linha |

São 8 opções (as 4 clássicas + lista/numerada/citação/inline code, de fev/2024).
**Não existe citação inline no WhatsApp** — logo, destacar só o número com um painel de
citação é impossível no destino final, independente do que o Telegram aceite.

### 2.2 Aninhamento (verificado empiricamente no canal, msgs 144-147)

`<b><code>$107.90</code></b>` foi enviado e o Telegram **descartou o negrito** — as
entities de retorno trouxeram só `code`. Bate com a regra documentada da Bot API ("bold
pode conter qualquer entity exceto `pre` e `code`") e com a documentação do WhatsApp
("monospace remove os outros formatos").

**Consequência:** um número é *ou* pastilha mono *ou* negrito. Nunca os dois, em nenhum
dos dois apps.

### 2.3 Tamanho (medido com dados reais, símbolos Platts de produção)

| workflow | cru | HTML | HTML + bloco copiável | cabe em 4096? |
|---|---:|---:|---:|---|
| `daily_report` | 304 | 418 | 776 | sim |
| `baltic` | 650 | 885 | 1601 | sim |
| `morning_check` | 2260 | 2722 | **5099** | **não** |

Tirar o blockquote economiza pouco (2156 cru na variante escolhida) — o `morning_check`
**continua estourando**. O split é obrigatório, não opcional.

Overhead de tags medido: **~20%**.

## 3. Decisões travadas

1. **Linha sem blockquote, nome sem negrito, número em mono** (Opção B).
   Motivo: com 22 linhas no `morning_check`, negrito em toda linha compete com os títulos
   de seção e achata a hierarquia. Na B o negrito fica reservado aos títulos e a pastilha
   mono vira o único destaque da linha.
2. **Header canônico de 4 linhas nos 3 crons**, igualando ao que o Curator já usa.
   Resolve de quebra o fato de os 3 crons se anunciarem como "DAILY REPORT" hoje, mesmo
   sendo relatórios diferentes (abertura, frete, futuros).
3. **Marcadores `↑ ↓ ·` em todos.** SGX e Baltic mudam (hoje `🟢 🔴 ▪️`); Platts fica.
4. **Separador decimal permanece ponto nos crons** (`$98.20`). As notícias seguem com
   vírgula PT-BR (`$107,90`), como o Curator exige.
5. **Bloco copiável** anexado ao post, com split automático em 2 mensagens quando estoura.

### 3.1 Inconsistência consciente

A decisão 4 deixa o canal com dois padrões numéricos: crons com ponto, notícias com
vírgula — e o header dos crons em PT-BR (`30/JUL`) convivendo com números em formato
inglês. Escolha explícita do usuário (2026-07-30) por não mexer no parsing a jusante.
Registrado aqui pra não ser relido como descuido.

## 4. Formato da linha

### Antes

```
> *Brazilian Blend Fines CFR Qingdao $/DMT*  `$107.90`  -0.55 (-0.51%) ↓
```

Vira `<blockquote>` com nome em negrito e preço em `<code>` — painel cinza inteiro.

### Depois

```
Brazilian Blend Fines CFR Qingdao $/DMT  `$107.90`  -0.55 (-0.51%) ↓
```

Nome em texto normal, preço na pastilha mono (único cinza da linha), variação em texto
normal, marcador no fim. Idêntico no Telegram e no WhatsApp, porque a fonte é a mesma.

## 5. Header unificado

```
📊 *MINERALS TRADING*
*<Título do relatório>*
`<ATIVO> · <DD/MMM>`
─────────────────
```

| Workflow | Título | Pílula |
|---|---|---|
| `daily_report` | `Curva SGX — Iron Ore 62% Fe` | `IRON ORE FUTURES · 30/JUL` |
| `morning_check` | `Assessments Platts — Abertura` | `IRON ORE · 30/JUL` |
| `baltic_ingestion` | `Baltic Exchange — Frete` | `FRETE · 30/JUL` |
| `market_news` | dinâmico (Writer) | dinâmico (Curator) — **sem mudança** |

Meses PT-BR em CAPS de 3 letras: `JAN FEV MAR ABR MAI JUN JUL AGO SET OUT NOV DEZ`.
A divisória é a única da mensagem, como já manda o prompt do Curator.

### 5.1 Divergência assumida com o Curator

O prompt do Curator proíbe emojis no corpo ("o único é 📊 do header"). Os crons **mantêm**
os emojis de seção (`🪨 *FINES*`, `⚓ *ROTAS CAPESIZE*`, `🌊 *BALTIC DRY INDEX*`).

Motivo: a regra do Curator existe porque notícia é prosa, onde emoji polui. Painel de
cotação é tabela — o emoji de seção é âncora de varredura em 34 linhas. Header e linha
ficam unificados; a regra de emoji não.

**Este é o ponto mais discutível do spec.** Se o usuário quiser uniformidade total, basta
remover os emojis de seção dos 3 crons.

## 6. Bloco copiável

Anexado ao post no `channel_delivery.py`, que é o ponto único por onde os 5 workflows
passam. O conteúdo do bloco é o **texto-fonte intocado** — ele já é sintaxe WhatsApp
nativa, então não há conversão nem segunda fonte de verdade a manter em sincronia.

```
texto cru ──┬─→ to_telegram_html()   → parte bonita (como hoje)
            └─→ escape + <pre>       → bloco copiável (novo)
                                        ↓
                         cabe em 4096? ─┬─ sim → 1 mensagem
                                        └─ não → 2 mensagens
```

- **Rótulo:** `📋 Copiar pro WhatsApp:` acima do bloco.
- **Gate:** pula o bloco quando o texto não tem nenhum marcador WhatsApp. Isso exclui
  `platts_reports` (posta só `📄 nome.pdf` + o PDF anexo) sem precisar de caso especial.
- **Split:** quando `pretty + bloco > 4096`, saem 2 mensagens — o post legível e depois o
  bloco. Nunca trunca conteúdo; um relatório longo custa uma mensagem extra, não linhas.
- **Guarda:** se o bloco sozinho passar de 4096 (escape de `&<>` inflando), manda só o
  post legível em vez de um bloco que o Telegram rejeitaria.

### 6.1 Retorno de `post_report_to_channel`

O contrato `{"ok", "message_id", "error"}` é preservado. No split, `message_id` é o da
**primeira** mensagem (o post legível). Falha ao postar o bloco mantém `ok=True` e
registra o problema em `error` — mesma postura que a do PDF hoje (§5 do spec do PR #3):
o acessório não derruba o principal.

## 7. Bug latente corrigido junto

`RAW_TEXT_LIMIT = 3500` (`channel_delivery.py:25`) trunca o texto cru assumindo que a
conversão HTML cabe em 4096. Com os 20% de overhead medidos, 3500 crus viram ~4230 — acima
do teto. Nenhuma mensagem atual chega perto, então nunca estourou, mas está armado.

**Correção:** baixar para `3300` (3300 × 1.21 ≈ 3993, com folga). Risco residual: um texto
com densidade de tags muito acima do normal ainda poderia estourar; aceitável dado o perfil
real das mensagens.

## 8. Escopo por superfície

| Superfície | Muda? | O quê |
|---|---|---|
| Preço do minério (SGX) | sim | linha B + header + marcador `↑↓·` |
| Dados Platts (abertura) | sim | linha B + header |
| Frete (Baltic) | sim | linha B + header + marcador `↑↓·` |
| Notícias (Curator) | **não** | já usa header canônico, bullets com mono nos números, e `>` só pra citação real |
| Relatórios PDF | não | sem linhas de cotação; gate pula o bloco copiável |
| Bloco copiável | novo | vale pros 5 de graça (ponto único) |

## 9. Arquitetura

Módulo novo `execution/core/report_format.py` — hoje os 3 crons duplicam a lógica de
linha e de marcador. Extrair evita que a próxima mudança estética precise de 3 edições
idênticas.

```python
MONTHS_PT_ABBR   # 1..12 -> 'JAN'..'DEZ' (pílula do header)
MONTHS_EN_TO_PT  # 'FEB' -> 'Fev'        (rótulo de contrato, só daily_report)

build_header(title: str, asset: str, when: date) -> str
format_row(name: str, value: str, stats: str, marker: str) -> str
marker_for(change: float) -> str   # ↑ | ↓ | ·
```

Consumidores: `send_daily_report.py`, `morning_check.py`, `baltic_ingestion.py`.

No `channel_delivery.py`:

```python
TELEGRAM_TEXT_LIMIT = 4096
COPY_LABEL = "📋 Copiar pro WhatsApp:"

has_whatsapp_markers(text: str) -> bool
build_copy_block(raw: str) -> str
build_channel_payload(raw: str) -> list[str]
```

`post_report_to_channel` passa a iterar sobre `build_channel_payload(message)`.

## 10. Testes

**Quebram e precisam de atualização** (esperado — o formato da linha é o que muda):

- `tests/test_daily_report_format.py` — 3 asserções em `> *`
- `tests/test_morning_check_format.py` — 3 asserções em `> *`
- `tests/test_baltic_format.py` — 8 asserções em `> *`

**Novos:**

- `tests/test_report_format.py` — header (meses PT-BR, pílula, divisória única), linha B,
  `marker_for` nas 3 faixas.
- `tests/test_channel_delivery.py` (+casos) — bloco presente com o texto cru exato; escape
  de `&<>` dentro do `<pre>`; split acima de 4096; gate pulando texto sem marcadores;
  guarda do bloco gigante; `message_id` da primeira mensagem no split; never-raise
  preservado.

## 11. Riscos

| Risco | Mitigação |
|---|---|
| Botão de copiar em `<pre>` é comportamento do app, não da Bot API — não verificável por código | Smoke no canal real antes do merge. Sem botão, seleção manual ainda entrega o texto literal certo. |
| Limite da API Anthropic estourado até 01/08 00:00 UTC | Não bloqueia: a mudança é 100% camada de entrega e formatadores determinísticos. O caminho Writer/Curator não muda, mas só dá pra validar ponta-a-ponta depois de 01/08. |
| 4 membros reais no canal durante os smokes | Mensagens rotuladas `🧪 TESTE`, sem notificação, apagadas ao fim. |
| Split dobra o volume de posts do `morning_check` | Só nele; os outros 4 seguem em 1 mensagem. |

## 12. Não-objetivos

- Não mexer no prompt do Curator (notícias já estão no formato-alvo).
- Não encurtar as descrições dos símbolos Platts — foi cogitado pra caber em 1 mensagem,
  mas não economiza o suficiente e mudaria o conteúdo do relatório.
- Não trocar o separador decimal (decisão 4).
- Não aposentar o caminho uazapi — segue como rollback via `CLIENT_DELIVERY_CHANNEL`.
