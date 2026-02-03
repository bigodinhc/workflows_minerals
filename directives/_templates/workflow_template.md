# Directive: [Nome do Workflow]

## Trigger
- Type: manual
- Config: N/A

## Inputs
| Nome | Tipo | Obrigatório | Descrição |
|------|------|-------------|-----------|
| example | string | sim | Exemplo de input |

## Steps

1. **Preparar dados**
   - Tool: `execution/script.py`
   - On Error: retry 3x with 5s backoff

2. IF dados_validos:
   - THEN: Continuar para passo 3
   - ELSE: Retornar erro de validação

3. FOR EACH item in lista:
   - Processar item
   - Tool: `execution/process_item.py`

4. **Salvar resultados**
   - Tool: `execution/save_results.py`
   - Output: Google Sheets

## Outputs
| Nome | Tipo | Descrição |
|------|------|-----------|
| result | object | O resultado processado |
| sheet_url | string | URL da planilha gerada |

## State
- Persists: last_cursor, processed_count
- Context: current_batch, errors

## Dependencies
- Nenhuma

## Edge Cases
- API rate limit → Aplicar retry com backoff exponencial
- Dados inválidos → Logar warning e pular item
- Falha total → Enviar notificação e parar workflow
