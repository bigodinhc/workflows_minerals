# Delivery Reporting System — Design Doc

**Data:** 2026-04-13
**Autor:** Colaboracao Claude + bigodinhc
**Status:** Draft — aguardando review

---

## 1. Problema

Hoje o sistema envia mensagens de WhatsApp para ~100 contatos por workflow (via UazAPI), mas:

1. **GitHub Actions:** workflows rodam automaticamente (`morning_check`, `baltic_ingestion`, `daily_report`, `send_news`) e enviam WhatsApp sem notificar o usuario do resultado. O status so esta visivel quem checar os logs do GitHub Actions manualmente.
2. **Webhook (Railway):** envia resumo minimo no Telegram (`X enviados, Y falhas`) sem identificar **quais contatos** falharam nem **por que**.
3. **Dashboard:** mostra os logs do GitHub Actions como texto cru, sem estrutura. Dificil auditar quem recebeu/nao recebeu.

Resultado: falhas de entrega podem passar despercebidas, e diagnostico exige abrir logs longos do GH Actions.

## 2. Objetivos

- Notificar o usuario via Telegram **imediatamente ao final** de todo envio (webhook **e** GH Actions), com status detalhado por contato que falhou.
- No dashboard, substituir o log cru por uma view estruturada mostrando total/OK/falha + lista de falhas com motivo.
- Zero persistencia nova (sem Supabase, sem DB) — usar logs do GH Actions (retidos 90 dias) como fonte de verdade.
- Um unico modulo compartilhado (`DeliveryReporter`) reusado por todos os 5 pontos de envio (elimina duplicacao de loop existente).

## 3. Nao-Objetivos

- Persistencia de longo prazo do historico (fora de escopo — usar GH Actions logs).
- Retry inteligente em falhas (ja existe via `UazapiClient` com backoff).
- Alteracao de `rationale_ingestion.py` e `market_news_ingestion.py` — eles nao enviam WhatsApp direto (delegam ao webhook).
- Novo sistema de autenticacao do webhook (problema separado, fora deste spec).

## 4. Arquitetura

```
GH Actions / Webhook (Flask)
    │
    ▼
DeliveryReporter.dispatch(contacts, message)
    │
    ├─► loop: send_fn(phone, text) → acumula Result(name, phone, success, error, duration)
    │
    ├─► stdout: <<<DELIVERY_REPORT_START>>> {JSON estruturado} <<<DELIVERY_REPORT_END>>>
    │         │
    │         └─► GH Actions logs (90d retencao)
    │                   │
    │                   └─► Dashboard /api/delivery-report → parseia JSON → render estruturado
    │
    └─► Telegram Bot API (sendMessage) → alerta humano imediato
              │
              └─► link: https://workflows-minerals.vercel.app/?run_id=<GH_RUN_ID>
```

### Localizacao do modulo
`execution/core/delivery_reporter.py` — convive com `logger.py`, `runner.py`, `state.py`.

## 5. API do `DeliveryReporter`

```python
# execution/core/delivery_reporter.py

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional
from datetime import datetime

@dataclass
class Contact:
    name: str
    phone: str

@dataclass
class DeliveryResult:
    contact: Contact
    success: bool
    error: Optional[str]    # "timeout" | "HTTP 400: ..." | "rate_limit" | None
    duration_ms: int

@dataclass
class DeliveryReport:
    workflow: str
    started_at: datetime
    finished_at: datetime
    results: list[DeliveryResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for r in self.results if not r.success)

    @property
    def failures(self) -> list[DeliveryResult]:
        return [r for r in self.results if not r.success]


class DeliveryReporter:
    def __init__(
        self,
        workflow: str,
        send_fn: Callable[[str, str], None],
        notify_telegram: bool = True,
        telegram_chat_id: Optional[str] = None,
        dashboard_base_url: str = "https://workflows-minerals.vercel.app",
        gh_run_id: Optional[str] = None,         # pra gerar link; None = link pra home
    ): ...

    def dispatch(
        self,
        contacts: Iterable[Contact],
        message: str,
        on_progress: Optional[Callable[[int, int, DeliveryResult], None]] = None,
    ) -> DeliveryReport:
        """
        Envia message pra cada contato.
        Side effects:
          - stdout: bloco JSON delimitado
          - Telegram: msg de resumo (se notify_telegram)
          - on_progress: callback chamado apos cada envio (processed, total, last_result)

        Nao levanta excecao se send falhar — captura como result.error.
        Nao levanta se Telegram falhar — loga warning.
        """
```

### Contrato do `send_fn`
Funcao que recebe `(phone: str, text: str)` e **levanta excecao em falha**. O reporter captura e categoriza em `error: str`:
- `requests.Timeout` → `"timeout"`
- `requests.HTTPError` → `"HTTP {code}: {body_first_100_chars}"`
- Outras `Exception` → `str(exception)[:200]`

## 6. Formato do JSON no stdout

```
<<<DELIVERY_REPORT_START>>>
{
  "workflow": "morning_check",
  "started_at": "2026-04-13T14:30:00-03:00",
  "finished_at": "2026-04-13T14:31:22-03:00",
  "duration_seconds": 82,
  "summary": {
    "total": 100,
    "success": 97,
    "failure": 3
  },
  "results": [
    {
      "name": "João Silva",
      "phone": "5511999999999",
      "success": true,
      "error": null,
      "duration_ms": 340
    },
    {
      "name": "Carlos Mendes",
      "phone": "5511888888888",
      "success": false,
      "error": "timeout",
      "duration_ms": 30000
    }
  ]
}
<<<DELIVERY_REPORT_END>>>
```

**Decisoes:**
- Marcadores `<<<...>>>` sobrevivem a prefixos de timestamp/cor do GH Actions.
- JSON em formato legivel (pretty-printed) — facilita debug direto nos logs.
- ISO 8601 com timezone pra evitar ambiguidade UTC/BRT.
- Telefone **nao mascarado** (decisao explicita do usuario).

## 7. Formato da Mensagem Telegram

### 7.1 — Sem falhas
```
✅ MORNING CHECK
13/04/2026 14:31 (1m 22s)

📊 Total: 100 | OK: 100 | Falha: 0

Todos os contatos receberam.

[Ver no dashboard](https://workflows-minerals.vercel.app/?run_id=12345)
```

### 7.2 — Com falhas (1-50%)
```
⚠️ MORNING CHECK
13/04/2026 14:31 (1m 22s)

📊 Total: 100 | OK: 97 | Falha: 3

❌ FALHAS:
• Carlos Mendes (5511888888888) — timeout
• Maria Lima (5511777777777) — HTTP 400: invalid number
• Roberto Alves (5511666666666) — rate_limit

[Ver no dashboard](https://workflows-minerals.vercel.app/?run_id=12345)
```

### 7.3 — Falha total (>50%)
```
🚨 MORNING CHECK — FALHA TOTAL
13/04/2026 14:31

📊 Total: 100 | OK: 0 | Falha: 100

Todos os envios falharam. Verifique:
• Token UAZAPI
• Status do servico UazAPI
• Logs do GitHub Actions

Primeira falha: timeout

[Ver no dashboard](https://workflows-minerals.vercel.app/?run_id=12345)
```

### Regras
- Emoji do header: `✅` (0 falhas), `⚠️` (1-50%), `🚨` (>50%)
- Se lista de falhas >15, exibe 15 primeiras + `...e mais N falhas`
- Parse mode: `Markdown`
- Webhook (sem `gh_run_id`): link vai pra `https://workflows-minerals.vercel.app/`
- Chat destino: env var `TELEGRAM_CHAT_ID` (ja usado no projeto)

## 8. Mudancas no Dashboard

### 8.1 — Novo endpoint `GET /api/delivery-report?run_id=X`
- Reusa a logica de busca de logs do `/api/logs`
- Regex extrai bloco entre `<<<DELIVERY_REPORT_START>>>` e `<<<DELIVERY_REPORT_END>>>`
- Retorna `{ found: boolean, report: DeliveryReport | null }`

### 8.2 — Componente `<DeliveryReportView>` no modal de logs
- Renderizado no topo do `Sheet` quando ha relatorio
- Sumario visual: 3 cards (Total / OK / Falha)
- Lista de falhas: tabela nome / telefone / erro
- Lista de sucessos: colapsavel (default fechado)
- Raw log do GH Actions: colapsavel (default fechado, fallback quando `found: false`)

### 8.3 — Auto-abrir via query param
- `https://workflows-minerals.vercel.app/?run_id=X` abre o modal automaticamente
- `useSearchParams` detecta, dispara `handleViewLogs(run_id)`

### 8.4 — **Nao** alterar
- Tabela "Execution Log" — nao adicionar contadores inline (caro: 1 API call por run)
- Outras paginas (`/contacts`, `/executions`, etc — fora de escopo)

## 9. Pontos de Integracao (refactor)

| Arquivo | Linhas atuais | `workflow` tag | Callback `on_progress`? |
|---------|---------------|----------------|-------------------------|
| `execution/scripts/morning_check.py` | 242-280 | `morning_check` | Nao |
| `execution/scripts/send_daily_report.py` | 131-165 | `daily_report` | Nao |
| `execution/scripts/baltic_ingestion.py` | 262-283 | `baltic` | Nao |
| `execution/scripts/send_news.py` | 50-78 | `manual_news` | Nao |
| `webhook/app.py::process_approval_async` | 768-820 | `webhook_approval` | **Sim** (edita msg Telegram de progresso a cada 10) |

### Exemplo de refactor (`morning_check.py`)
```python
# ANTES
for contact in contacts:
    phone = ...
    try:
        uazapi.send_message(phone, message)
        success += 1
    except Exception as e:
        logger.error(...)

# DEPOIS
from execution.core.delivery_reporter import DeliveryReporter, Contact

reporter = DeliveryReporter(
    workflow="morning_check",
    send_fn=uazapi.send_message,
    gh_run_id=os.getenv("GITHUB_RUN_ID"),
)
report = reporter.dispatch(
    contacts=[Contact(name=c.get("Nome", "—"), phone=c["Telefone"]) for c in raw_contacts],
    message=message,
)
sys.exit(0 if report.failure_count == 0 else 1)
```

### Webhook (`process_approval_async`) — preserva progresso intermediario
```python
def on_progress(processed, total, last_result):
    if processed % 10 == 0:
        edit_message(chat_id, progress_msg_id,
            f"⏳ Enviando... {processed}/{total}")

reporter = DeliveryReporter(
    workflow="webhook_approval",
    send_fn=lambda phone, text: _send_whatsapp_raise(phone, text, token, url),
    gh_run_id=None,  # webhook sem GH run
)
reporter.dispatch(contacts, draft_message, on_progress=on_progress)
# nao precisa mais enviar msg final manual — reporter ja faz
```

Obs: `send_whatsapp()` do webhook hoje retorna `bool`. Envolver em wrapper que **levanta** em falha pra seguir contrato do `send_fn`.

## 10. Testes

Arquivo: `tests/test_delivery_reporter.py`

| Caso | Descricao |
|------|-----------|
| `test_dispatch_all_success` | 5 contatos, mock sempre OK → total=5, success=5, failure=0 |
| `test_dispatch_partial_failure` | Mock falha em indices 1 e 3 → success=3, failure=2 |
| `test_dispatch_all_failure` | Mock sempre levanta → emoji 🚨 na msg Telegram |
| `test_json_output_format` | Captura stdout, verifica marcadores + JSON parseavel + schema |
| `test_telegram_message_truncation` | 50 falhas → msg tem 15 listadas + "...e mais 35" |
| `test_on_progress_callback` | 10 contatos → callback chamado 10 vezes |
| `test_telegram_failure_does_not_abort` | Mock Telegram falha → dispatch() ainda retorna report valido |
| `test_error_categorization` | Timeout vira "timeout", HTTPError vira "HTTP {code}: ..." |

Sem testes de integracao real (mockado tudo).

## 11. Atualizacao Paralela do Modelo Claude

Nao faz parte do `DeliveryReporter`, mas foi solicitado junto. Trocar 3 referencias:

| Arquivo | Linha | Antes | Depois |
|---------|-------|-------|--------|
| `execution/integrations/claude_client.py` | 52 | `claude-sonnet-4-20250514` | `claude-sonnet-4-6` |
| `execution/integrations/claude_client.py` | 94 | `claude-3-haiku-20240307` | `claude-sonnet-4-6` |
| `webhook/app.py` | 660 | `claude-sonnet-4-20250514` | `claude-sonnet-4-6` |

## 12. Plano de Rollout

1. **Implementar e testar `DeliveryReporter`** (modulo + testes unitarios)
2. **Refatorar `morning_check.py`** (script mais simples, valida o design na pratica)
3. **Refatorar demais scripts** (`baltic`, `daily_report`, `send_news`)
4. **Refatorar `webhook/app.py::process_approval_async`** (mais complexo — callback de progresso)
5. **Implementar `/api/delivery-report` + `<DeliveryReportView>`** no dashboard
6. **Atualizar modelo Claude** (3 trocas simples)
7. **Deploy:** webhook → Railway; workflows → GH Actions automaticamente no proximo cron

Cada etapa e testavel isoladamente. Se qualquer refactor quebrar, fallback e reverter apenas aquele arquivo.

## 13. Riscos e Mitigacoes

| Risco | Probabilidade | Mitigacao |
|-------|---------------|-----------|
| GH Actions logs nao conservam marcadores (stripping) | Baixa | Marcadores sao ASCII puros, sobrevivem a qualquer formatacao |
| Telegram rate limit (30 msg/s) | Muito baixa | 1 msg por workflow, cron e espacado |
| `send_fn` que nao levanta em falha (UazapiClient retorna None mas pode nao levantar em HTTP != 2xx) | Media | Validar comportamento do `UazapiClient.send_message` durante implementacao; wrap se necessario |
| Dashboard `?run_id=X` causa loop de render ou SSR issue | Baixa | Usar `useSearchParams` + `useEffect` com deps corretas |
| Quebra de `process_approval_async` (fluxo do usuario ativo) | Media | Implementar por ultimo, testar com um draft real antes de deploy |

## 14. Questoes em Aberto

Nenhuma. Todas as decisoes foram acordadas com o usuario:
- [x] Abordagem: modulo compartilhado (Abordagem 1)
- [x] Formato Telegram: compacto com falhas detalhadas + link
- [x] Persistencia: nao (usar logs do GH Actions)
- [x] Telefone: nao mascarar
- [x] URL dashboard: `https://workflows-minerals.vercel.app/`
- [x] Modelo Claude: `claude-sonnet-4-6`
