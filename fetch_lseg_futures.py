#!/usr/bin/env python3
"""
Script de coleta de derivativos de minerio de ferro (SGX Iron Ore Swaps) do LSEG.

Coleta:
    - SGX Iron Ore 62% Fe Swaps (cadeia 0#SZZF:)
    - Campos: CF_LAST (preco), EXPIR_DATE (vencimento)
    - Contratos: C1 a C12 (12 meses forward)

Uso:
    # Coleta snapshot atual (teste)
    python fetch_lseg_futures.py --mode snapshot

    # Coleta historico completo
    python fetch_lseg_futures.py --mode historical --start-date 2019-01-01 --end-date 2026-01-07 --to-supabase

    # Coleta e insere no Supabase
    python fetch_lseg_futures.py --to-supabase

    # Sem salvar CSV
    python fetch_lseg_futures.py --to-supabase --skip-csv

Requisitos:
    - Credenciais LSEG Platform configuradas em lseg-data.config.json
"""

import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# Carregar .env do diretorio jobs/
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Importar lseg.data
try:
    import lseg.data as ld
except ImportError:
    print("ERRO: lseg-data nao instalado. Execute: pip install lseg-data")
    sys.exit(1)


# =============================================================================
# CONFIGURACAO
# =============================================================================

# Campos a coletar (CF_LAST nao funciona, usar SETTLE/TRDPRC_1)
FIELDS = ["SETTLE", "TRDPRC_1", "BID", "ASK", "EXPIR_DATE"]

# Maximo de contratos a coletar (C1-C12)
MAX_CONTRACTS = 12

# RICs de continuation (rolling front-month)
# SZZFc1 = front-month, SZZFc2 = second month, etc.
CONTINUATION_RICS = ["SZZFc1", "SZZFc2", "SZZFc3"]

# Source para Supabase (diferenciado por tipo)
SOURCE_INTRADAY = "lseg_futures_intraday"  # Precos em tempo real (TRDPRC_1)
SOURCE_SETTLE = "lseg_futures_settle"      # Precos de fechamento (SETTLE)

# Mapeamento de meses para codigo RIC
MONTH_CODES = {
    1: "F",   # January
    2: "G",   # February
    3: "H",   # March
    4: "J",   # April
    5: "K",   # May
    6: "M",   # June
    7: "N",   # July
    8: "Q",   # August
    9: "U",   # September
    10: "V",  # October
    11: "X",  # November
    12: "Z",  # December
}


def generate_futures_rics(num_months: int = 12) -> list:
    """
    Gera lista de RICs para os proximos N meses de contratos.

    Returns:
        Lista de RICs como ['SZZFF6', 'SZZFG6', ...]
    """
    from datetime import date
    from dateutil.relativedelta import relativedelta

    rics = []
    today = date.today()

    for i in range(num_months):
        target_date = today + relativedelta(months=i)
        year = target_date.year
        month = target_date.month

        # Codigo do ano (ultimo digito)
        year_code = str(year)[-1]

        # Codigo do mes
        month_code = MONTH_CODES[month]

        # RIC: SZZF + month + year
        ric = f"SZZF{month_code}{year_code}"
        rics.append(ric)

    return rics


def generate_historical_rics(start_date, end_date) -> list:
    """
    Gera lista de RICs para contratos historicos entre duas datas.

    Gera 12 contratos forward para cada mes no periodo.

    Args:
        start_date: Data inicial (string ou date)
        end_date: Data final (string ou date)

    Returns:
        Lista de RICs unicos para o periodo
    """
    from datetime import date
    from dateutil.relativedelta import relativedelta

    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)

    rics_set = set()
    current = start_date

    # Para cada mes no periodo, gerar os 12 contratos forward
    while current <= end_date:
        for i in range(12):
            target_date = current + relativedelta(months=i)
            year = target_date.year
            month = target_date.month

            # Codigo do ano (ultimo digito)
            year_code = str(year)[-1]

            # Codigo do mes
            month_code = MONTH_CODES[month]

            # RIC: SZZF + month + year
            ric = f"SZZF{month_code}{year_code}"
            rics_set.add(ric)

        current += relativedelta(months=1)

    return sorted(list(rics_set))


# =============================================================================
# FUNCOES DE COLETA
# =============================================================================

def open_lseg_session():
    """
    Abre sessao LSEG usando arquivo de configuracao.

    O arquivo lseg-data.config.json deve estar no diretorio jobs/
    com as credenciais Platform (app-key, username, password).
    """
    print("Conectando ao LSEG...")

    # Buscar arquivo de configuracao
    config_path = Path(__file__).parent.parent / "lseg-data.config.json"

    if not config_path.exists():
        print(f"\nERRO: Arquivo de configuracao nao encontrado!")
        print(f"  Esperado: {config_path}")
        print("\nCrie o arquivo com o seguinte formato:")
        print('''
{
    "sessions": {
        "default": "platform.ldp",
        "platform": {
            "ldp": {
                "app-key": "SEU_APP_KEY",
                "username": "SEU_USERNAME",
                "password": "SUA_SENHA",
                "signon_control": true
            }
        }
    }
}
''')
        return False

    try:
        print(f"  Usando config: {config_path.name}")
        ld.open_session(config_name=str(config_path))

        # Testar conexao com query simples
        print("  Testando conexao...", end=" ", flush=True)
        test_df = ld.get_history(
            universe="EUR=",
            fields=["BID"],
            interval="daily",
            count=1
        )

        if test_df is None or test_df.empty:
            raise Exception("Conexao OK mas sem dados de teste")

        print("OK!")
        print("Conectado via Platform Session")
        return True

    except Exception as e:
        print(f"\nERRO ao conectar ao LSEG: {e}")
        print("\nVerifique:")
        print("  1. Credenciais no lseg-data.config.json estao corretas")
        print("  2. Sua conta LSEG tem acesso a API Platform")
        print("  3. Conexao com internet esta funcionando")
        return False


def close_lseg_session():
    """Fecha sessao LSEG."""
    try:
        ld.close_session()
        print("Sessao LSEG fechada")
    except Exception:
        pass


def fetch_futures_chain() -> pd.DataFrame:
    """
    Busca dados dos contratos futuros de minerio de ferro.

    Gera RICs individuais para os proximos 12 meses e busca cada um.

    Returns:
        DataFrame com RIC, SETTLE/TRDPRC_1, EXPIR_DATE
    """
    try:
        # Gerar RICs para os proximos 12 meses
        rics = generate_futures_rics(MAX_CONTRACTS)

        print(f"\nBuscando {len(rics)} contratos futuros...")
        print(f"  RICs: {', '.join(rics[:6])}...")
        print(f"  Campos: {FIELDS}")

        # Buscar todos os contratos de uma vez
        df = ld.get_data(
            universe=rics,
            fields=FIELDS
        )

        if df is None or df.empty:
            print("  ERRO: Nenhum dado retornado")
            return pd.DataFrame()

        # Resetar indice para ter RIC como coluna
        df = df.reset_index()

        # Renomear coluna de indice se necessario
        if "Instrument" in df.columns:
            df = df.rename(columns={"Instrument": "RIC"})
        elif "index" in df.columns:
            df = df.rename(columns={"index": "RIC"})

        # Remover linhas sem dados de preco
        price_cols = ["SETTLE", "TRDPRC_1", "BID", "ASK"]
        has_price = df[price_cols].notna().any(axis=1)
        df = df[has_price].reset_index(drop=True)

        print(f"  Contratos com dados: {len(df)}")

        return df

    except Exception as e:
        print(f"  ERRO ao buscar futuros: {e}")
        return pd.DataFrame()


def fetch_futures_historical(start_date: str, end_date: str, include_continuation: bool = False) -> pd.DataFrame:
    """
    Busca dados historicos dos contratos futuros de minerio de ferro.

    Args:
        start_date: Data inicial (YYYY-MM-DD)
        end_date: Data final (YYYY-MM-DD)
        include_continuation: Se True, inclui RICs de continuation (SZZFc1-c3)

    Returns:
        DataFrame com dados historicos de todos os contratos
    """
    try:
        # Gerar RICs para o periodo
        rics = generate_historical_rics(start_date, end_date)

        # Adicionar RICs de continuation se solicitado
        if include_continuation:
            rics = CONTINUATION_RICS + rics
            print(f"\n  Incluindo continuation RICs: {CONTINUATION_RICS}")

        print(f"\nBuscando historico de {len(rics)} contratos...")
        print(f"  Periodo: {start_date} a {end_date}")
        print(f"  RICs (amostra): {', '.join(rics[:6])}...")

        # Buscar historico - dividir em batches para evitar timeout
        BATCH_SIZE = 20
        all_dfs = []

        for i in range(0, len(rics), BATCH_SIZE):
            batch_rics = rics[i:i + BATCH_SIZE]
            print(f"  Buscando batch {i//BATCH_SIZE + 1}/{(len(rics) + BATCH_SIZE - 1)//BATCH_SIZE}...")

            try:
                df = ld.get_history(
                    universe=batch_rics,
                    fields=["SETTLE", "TRDPRC_1", "BID", "ASK"],
                    start=start_date,
                    end=end_date,
                    interval="daily"
                )

                if df is not None and not df.empty:
                    all_dfs.append(df)
                    print(f"    Registros: {len(df)}")

            except Exception as e:
                print(f"    WARN: Erro no batch: {e}")
                continue

        if not all_dfs:
            print("  ERRO: Nenhum dado retornado")
            return pd.DataFrame()

        # Concatenar todos os DataFrames
        df = pd.concat(all_dfs)

        # Resetar indice para ter Date e RIC como colunas
        df = df.reset_index()

        print(f"\n  Total registros historicos: {len(df)}")

        return df

    except Exception as e:
        print(f"  ERRO ao buscar historico: {e}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()


def transform_futures_historical(df: pd.DataFrame) -> pd.DataFrame:
    """
    Transforma DataFrame historico de futuros para formato price_points.

    Para dados historicos, priorizamos SETTLE (preco de fechamento oficial).

    O LSEG retorna dados em formato wide com colunas como:
        Date, SZZFF6_SETTLE, SZZFF6_TRDPRC_1, SZZFG6_SETTLE, ...

    Colunas de saida:
        - variable_key: DERIV_IO_SWAP_YYYY_MM
        - ts: Timestamp do dia
        - value: SETTLE (fechamento) ou fallback para outros
        - ric: RIC original
        - source: 'lseg_futures_settle'
    """
    if df.empty:
        return df

    # LSEG get_history retorna DataFrame com Date como index ou coluna
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    elif df.index.name in ['Date', 'date']:
        df = df.reset_index()

    # Flatten column names if MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ['_'.join(str(c) for c in col).strip('_') if isinstance(col, tuple) else col for col in df.columns]

    # Identificar coluna de Date
    date_col = None
    for col in df.columns:
        if str(col).lower() == 'date':
            date_col = col
            break

    if date_col is None:
        print("  WARN: Coluna Date nao encontrada")
        print(f"  Colunas disponiveis: {df.columns.tolist()}")
        return pd.DataFrame()

    # Extrair RICs unicos das colunas (formato: RIC_FIELD)
    # Ex: SZZFF6_SETTLE -> SZZFF6
    rics = set()
    for col in df.columns:
        if col == date_col:
            continue
        parts = str(col).split('_')
        if len(parts) >= 2 and parts[0].startswith('SZZF'):
            rics.add(parts[0])

    print(f"  RICs encontrados: {len(rics)}")

    result_rows = []

    for _, row in df.iterrows():
        ts = row[date_col]
        if pd.isna(ts):
            continue

        for ric in rics:
            # Obter valor (tentar SETTLE primeiro, depois TRDPRC_1, BID, ASK)
            value = None
            for field in ["SETTLE", "TRDPRC_1", "BID", "ASK"]:
                col_name = f"{ric}_{field}"
                if col_name in row.index and pd.notna(row[col_name]):
                    value = row[col_name]
                    break

            if value is None:
                continue

            # Verificar se e RIC de continuation (SZZFcN onde N = 1, 2, 3...)
            if ric.startswith("SZZFc") and len(ric) >= 6:
                # Continuation RIC: SZZFc1 -> SGX_IO_SWAP_C1
                contract_num = ric[5:]  # "1", "2", "3", etc.
                variable_key = f"SGX_IO_SWAP_C{contract_num}"

                result_rows.append({
                    "variable_key": variable_key,
                    "ts": pd.to_datetime(ts),
                    "value": float(value),
                    "ric": ric,
                    "source": SOURCE_SETTLE,
                })
                continue

            # RIC de contrato especifico (SZZFXY onde X=mes, Y=ano)
            # Ex: SZZFF6 -> F=Jan, 6=2026
            if len(ric) >= 6:
                month_code = ric[4]
                year_code = int(ric[5])

                # Converter codigo do mes para numero
                month_num = None
                for m, c in MONTH_CODES.items():
                    if c == month_code:
                        month_num = m
                        break

                if month_num is None:
                    continue

                # Converter codigo do ano considerando a decada correta
                # O ano do contrato deve ser >= ano do registro (futuros)
                ts_year = pd.to_datetime(ts).year
                base_decade = (ts_year // 10) * 10  # Ex: 2023 -> 2020
                year = base_decade + year_code

                # Se o ano calculado for menor que o ano do registro,
                # o contrato e da proxima decada
                if year < ts_year:
                    year += 10

                # Gerar variable_key
                variable_key = f"DERIV_IO_SWAP_{year}_{month_num:02d}"

                result_rows.append({
                    "variable_key": variable_key,
                    "ts": pd.to_datetime(ts),
                    "value": float(value),
                    "ric": ric,
                    "source": SOURCE_SETTLE,
                })

    result = pd.DataFrame(result_rows)

    if not result.empty:
        # Ordenar por variable_key e ts
        result = result.sort_values(["variable_key", "ts"]).reset_index(drop=True)

        # Remover duplicatas (mesmo variable_key e ts)
        result = result.drop_duplicates(subset=["variable_key", "ts"], keep="first")

        print(f"  Registros apos transformacao: {len(result)}")
        print(f"  Contratos unicos: {result['variable_key'].nunique()}")

    return result


def expiry_to_variable_key(expiry_date) -> str:
    """
    Converte data de expiracao para variable_key.

    Args:
        expiry_date: Data de expiracao (datetime, date, ou string)

    Returns:
        String no formato DERIV_IO_SWAP_YYYY_MM
    """
    if pd.isna(expiry_date):
        return None

    # Converter para datetime se necessario
    if isinstance(expiry_date, str):
        expiry_date = pd.to_datetime(expiry_date)

    # Extrair ano e mes
    year = expiry_date.year
    month = expiry_date.month

    return f"DERIV_IO_SWAP_{year}_{month:02d}"


def transform_futures(df: pd.DataFrame) -> pd.DataFrame:
    """
    Transforma DataFrame de futuros (snapshot intraday) para formato price_points.

    Para dados intraday, priorizamos TRDPRC_1 (preco de negociacao atual)
    em vez de SETTLE (preco de fechamento).

    Colunas de saida:
        - variable_key: DERIV_IO_SWAP_YYYY_MM
        - ts: Timestamp atual
        - value: TRDPRC_1 (preco atual) ou fallback para outros
        - ric: RIC original
        - expiry_date: Data de expiracao
        - source: 'lseg_futures_intraday'
    """
    if df.empty:
        return df

    result_rows = []
    ts_now = datetime.utcnow()

    for _, row in df.iterrows():
        # Obter valor (TRDPRC_1 primeiro para dados intraday)
        value = None
        for col in ["TRDPRC_1", "BID", "ASK", "SETTLE"]:
            if col in row.index and pd.notna(row[col]):
                value = row[col]
                break

        if value is None:
            continue

        # Obter data de expiracao
        expiry_date = row.get("EXPIR_DATE")
        if pd.isna(expiry_date):
            print(f"  WARN: Sem EXPIR_DATE para {row.get('RIC', '?')}")
            continue

        # Gerar variable_key
        variable_key = expiry_to_variable_key(expiry_date)
        if not variable_key:
            continue

        result_rows.append({
            "variable_key": variable_key,
            "ts": ts_now,
            "value": float(value),
            "ric": row.get("RIC"),
            "expiry_date": pd.to_datetime(expiry_date),
            "source": SOURCE_INTRADAY,
        })

    result = pd.DataFrame(result_rows)

    if not result.empty:
        # Ordenar por expiry_date
        result = result.sort_values("expiry_date").reset_index(drop=True)

    return result


def save_to_csv(df: pd.DataFrame, output_dir: str, filename: str = None):
    """Salva DataFrame em CSV."""
    if df.empty:
        print("Nada para salvar.")
        return

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"lseg_futures_{timestamp}.csv"

    filepath = output_path / filename
    df.to_csv(filepath, index=False)

    print(f"\nDados salvos em: {filepath}")
    print(f"  Tamanho: {filepath.stat().st_size / 1024:.1f} KB")


def ensure_variables_exist(df: pd.DataFrame):
    """
    Garante que as variaveis existam no Supabase antes de inserir price_points.

    Args:
        df: DataFrame com coluna variable_key
    """
    if df.empty:
        return

    # Importar cliente
    try:
        jobs_dir = Path(__file__).parent.parent
        if str(jobs_dir) not in sys.path:
            sys.path.insert(0, str(jobs_dir))

        from clients import SupabaseClient
    except ImportError as e:
        print(f"ERRO: Nao foi possivel importar SupabaseClient: {e}")
        sys.exit(1)

    client = SupabaseClient()

    # Preparar variaveis a criar
    variables = []
    for _, row in df.iterrows():
        var_key = row["variable_key"]
        expiry = row.get("expiry_date")

        # Formatar nome legivel (ex: "SGX Iron Ore Swap Jan 2026")
        if expiry and not pd.isna(expiry):
            expiry_dt = pd.to_datetime(expiry)
            name = f"SGX Iron Ore Swap {expiry_dt.strftime('%b %Y')}"
        else:
            name = var_key

        variables.append({
            "key": var_key,
            "name": name,
            "unit": "USD/dmt",
            "source": "workspace",
            "metadata": {"type": "swap", "exchange": "SGX", "ric": row.get("ric")}
        })

    # Inserir variaveis (upsert)
    print(f"\nGarantindo {len(variables)} variaveis no Supabase...")

    try:
        for var in variables:
            client.client.table("variables").upsert(
                var,
                on_conflict="key"
            ).execute()
        print(f"  Variaveis criadas/atualizadas: {len(variables)}")
    except Exception as e:
        print(f"  ERRO ao criar variaveis: {e}")
        # Continuar mesmo com erro - talvez ja existam


def insert_to_supabase(df: pd.DataFrame) -> int:
    """
    Insere dados no Supabase via upsert.

    Args:
        df: DataFrame com colunas ts, variable_key, value, source

    Returns:
        Numero de registros inseridos
    """
    if df.empty:
        print("Nenhum dado para inserir no Supabase.")
        return 0

    # Importar cliente apenas quando necessario
    try:
        jobs_dir = Path(__file__).parent.parent
        if str(jobs_dir) not in sys.path:
            sys.path.insert(0, str(jobs_dir))

        from clients import SupabaseClient
    except ImportError as e:
        print(f"ERRO: Nao foi possivel importar SupabaseClient: {e}")
        sys.exit(1)

    # Verificar credenciais
    if not os.getenv("SUPABASE_SERVICE_KEY"):
        print("ERRO: SUPABASE_SERVICE_KEY nao configurada no .env")
        sys.exit(1)

    # Garantir que variaveis existam
    ensure_variables_exist(df)

    # Preparar registros - usar source do DataFrame
    records = []
    source = df["source"].iloc[0] if "source" in df.columns else SOURCE_SETTLE
    for _, row in df.iterrows():
        records.append({
            "variable_key": row["variable_key"],
            "ts": row["ts"],
            "value": float(row["value"]),
        })

    print(f"\nInserindo {len(records)} registros no Supabase (source={source})...")

    try:
        client = SupabaseClient()
        count = client.upsert_price_points_batch(records, source=source)
        print(f"Registros inseridos/atualizados: {count}")
        return count
    except Exception as e:
        print(f"ERRO ao inserir no Supabase: {e}")
        sys.exit(1)


def print_futures_table(df: pd.DataFrame):
    """Imprime tabela formatada dos futuros."""
    if df.empty:
        return

    print("\n" + "=" * 70)
    print("CURVA DE FUTUROS - SGX IRON ORE SWAPS")
    print("=" * 70)

    ts = df["ts"].iloc[0] if "ts" in df.columns else datetime.now()
    print(f"Timestamp: {ts.strftime('%Y-%m-%d %H:%M:%S')} UTC\n")

    print(f"{'Variable Key':<25} {'Expiry':<12} {'Price':>10} {'RIC':<12}")
    print("-" * 70)

    for _, row in df.iterrows():
        var_key = row["variable_key"]
        expiry = row.get("expiry_date")
        value = row["value"]
        ric = row.get("ric", "")

        if expiry and not pd.isna(expiry):
            expiry_str = pd.to_datetime(expiry).strftime("%Y-%m-%d")
        else:
            expiry_str = "???"

        print(f"{var_key:<25} {expiry_str:<12} ${value:>9.2f} {ric:<12}")

    print("-" * 70)
    print(f"Total contratos: {len(df)}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Coleta derivativos de minerio de ferro (SGX Iron Ore Swaps) do LSEG"
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["snapshot", "historical"],
        default="snapshot",
        help="Modo de coleta: snapshot (atual) ou historical (historico)"
    )
    parser.add_argument(
        "--start-date",
        type=str,
        help="Data inicial para modo historical (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date",
        type=str,
        help="Data final para modo historical (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(Path(__file__).parent.parent / "output"),
        help="Diretorio de saida para CSV"
    )
    parser.add_argument(
        "--output-file",
        type=str,
        help="Nome do arquivo CSV (auto-gerado se nao especificado)"
    )
    parser.add_argument(
        "--to-supabase",
        action="store_true",
        help="Inserir dados no Supabase apos coleta"
    )
    parser.add_argument(
        "--skip-csv",
        action="store_true",
        help="Nao salvar CSV (apenas inserir no Supabase)"
    )
    parser.add_argument(
        "--include-continuation",
        action="store_true",
        help="Incluir RICs de continuation (SZZFc1-c3) para rolling front-month"
    )

    args = parser.parse_args()

    # Validar argumentos para modo historical
    if args.mode == "historical":
        if not args.start_date or not args.end_date:
            print("ERRO: Modo historical requer --start-date e --end-date")
            sys.exit(1)

    print("=" * 70)
    print("LSEG FUTURES FETCHER - SGX Iron Ore Swaps")
    print(f"Modo: {args.mode.upper()}")
    print("=" * 70)

    # Abrir sessao LSEG
    if not open_lseg_session():
        sys.exit(1)

    try:
        if args.mode == "snapshot":
            # Buscar dados da cadeia de futuros (snapshot atual)
            df = fetch_futures_chain()

            if df.empty:
                print("\nNenhum dado foi coletado. Verifique:")
                print("  - LSEG Workspace esta conectado")
                print("  - RIC chain esta correta")
                print("  - Sua licenca tem acesso a SGX Iron Ore Swaps")
                sys.exit(1)

            # Transformar dados
            df = transform_futures(df)

            if df.empty:
                print("\nERRO: Transformacao resultou em dados vazios")
                sys.exit(1)

            # Imprimir tabela
            print_futures_table(df)

        else:
            # Modo historical
            df = fetch_futures_historical(args.start_date, args.end_date, args.include_continuation)

            if df.empty:
                print("\nNenhum dado historico foi coletado. Verifique:")
                print("  - LSEG Workspace esta conectado")
                print("  - Periodo de datas esta correto")
                print("  - Sua licenca tem acesso a dados historicos")
                sys.exit(1)

            # Transformar dados historicos
            df = transform_futures_historical(df)

            if df.empty:
                print("\nERRO: Transformacao resultou em dados vazios")
                sys.exit(1)

            # Imprimir resumo
            print("\n" + "=" * 70)
            print("RESUMO DO HISTORICO")
            print("=" * 70)
            print(f"  Periodo: {args.start_date} a {args.end_date}")
            print(f"  Registros: {len(df)}")
            print(f"  Contratos unicos: {df['variable_key'].nunique()}")
            print(f"  Data mais antiga: {df['ts'].min()}")
            print(f"  Data mais recente: {df['ts'].max()}")

        # Salvar em CSV (a menos que --skip-csv)
        if not args.skip_csv:
            save_to_csv(df, args.output_dir, args.output_file)

        # Inserir no Supabase (se --to-supabase)
        if args.to_supabase:
            insert_to_supabase(df)

        print("\nColeta finalizada com sucesso!")

    finally:
        close_lseg_session()


if __name__ == "__main__":
    main()
