
import os
import sys
import pandas as pd
from datetime import datetime
from ..core.logger import WorkflowLogger

try:
    import spgci as ci
except ImportError:
    # Handle missing dependency gracefully usually, but here it's critical
    ci = None

class PlattsClient:
    # Mapeamento estático (Constant)
    # Mapeamento expandido (Baseado no JSON/Output desejado pelo usuário)
    # Key = Variavel interna (usaremos o proprio symbol para facilitar ou uma chave descritiva)
    # Value = Symbol da API Platts
    SYMBOLS_MAPPING = {
        # --- FINES ---
        "IOBBA00": "IOBBA00", # Brazilian Blend Fines CFR Qingdao
        "IODFE00": "IODFE00", # IO fines Fe 58%
        "IOPRM00": "IOPRM00", # IO fines Fe 65%
        "IOJBA00": "IOJBA00", # Jimblebar Fines CFR Qingdao
        "IOMAA00": "IOMAA00", # Mining Area C Fines CFR Qingdao
        "IONHA00": "IONHA00", # Newman High Grade Fines CFR Qingdao
        "IOPBQ00": "IOPBQ00", # Pilbara Blend Fines CFR Qingdao
        "IODBZ00": "IODBZ00", # IODEX CFR CHINA 62% Fe
        "TS01021": "TS01021", # TSI Iron Ore Fines 62% Fe CFR China
        
        # --- LUMP & PELLET ---
        "IODRP00": "IODRP00", # Iron Ore 67.5% Fe DR Pellet Premium
        "IOCQR04": "IOCQR04", # Iron Ore Blast Furnace 63% Fe Pellet CFR China
        "IOBFC04": "IOBFC04", # Iron Ore Blast Furnace Pellet Premium CFR China Wkly
        "IOCLS00": "IOCLS00", # Iron Ore Lump Outright Price CFR China
        
        # --- VIU DIFFERENTIALS ---
        "IOALE00": "IOALE00", # Alumina Diff 2.5-4%
        "TSIAF00": "TSIAF00", # Alumina Diff <5% (55-60% Fe)
        "TSIAD00": "TSIAD00", # Fe Diff within 55-60% Fe
        "IOPPQ00": "IOPPQ00", # Phos Diff 0.09-0.12%
        "IOPPT00": "IOPPT00", # Phos Diff 0.10-0.11%
        "IOPPU00": "IOPPU00", # Phos Diff 0.11-0.12%
        "IOPPV00": "IOPPV00", # Phos Diff 0.12-0.15%
        "IOALF00": "IOALF00", # Silica Diff 3-4.5%
        "TSIAI00": "TSIAI00", # Silica Diff 55-60% Fe
        "IOADF10": "IOADF10", # Alumina Diff 1-2.5%
        "IOPPS10": "IOPPS10", # Silica Diff 4.5-6.5%
        "IOPPS20": "IOPPS20", # Silica Diff 6.5-9%
        "IOMGD00": "IOMGD00", # Mid Range Diff 60-63.5 Fe
    }
    
    SYMBOL_BATE_OVERRIDE = {
        "IOMGD00": None,  # None = buscar todos os bates
    }

    def __init__(self):
        self.logger = WorkflowLogger("PlattsClient")
        if not ci:
            self.logger.critical("Library 'spgci' not installed.")
            raise ImportError("spgci not installed")
            
        self.username = os.getenv("SPGCI_USERNAME")
        self.password = os.getenv("SPGCI_PASSWORD")
        
        if not self.username or not self.password:
            self.logger.error("Missing SPGCI credentials.")
            raise ValueError("Missing SPGCI_USERNAME or SPGCI_PASSWORD")

    def fetch_symbol_data(self, symbol: str, date: str, bate: str = "c") -> pd.DataFrame:
        """
        Busca dados de um símbolo para UMA data específica.
        """
        try:
            mdd = ci.MarketData()
            effective_bate = self.SYMBOL_BATE_OVERRIDE.get(symbol, bate)
            
            kwargs = {
                "symbol": symbol,
                "assess_date_gte": date,
                "assess_date_lte": date,
                "paginate": True
            }
            
            if effective_bate is not None:
                kwargs["bate"] = effective_bate
                
            df = mdd.get_assessments_by_symbol_historical(**kwargs)
            
            if df is None or df.empty:
                return pd.DataFrame()
            
            return df
            
        except Exception as e:
            self.logger.error(f"Error fetching {symbol}", {"error": str(e)})
            return pd.DataFrame()

    def get_report_data(self, target_date: datetime) -> list:
        """
        Coleta dados do dia alvo e do dia útil anterior para calcular variações via Polling.
        Retorna lista pronta para o relatório.
        """
        # Calcular dia útil anterior para comparação
        weekday = target_date.weekday()
        if weekday == 0: # Monday -> Friday
            prev_date = target_date - pd.Timedelta(days=3)
        elif weekday == 6: # Sunday -> Friday
            prev_date = target_date - pd.Timedelta(days=2)
        else:
            prev_date = target_date - pd.Timedelta(days=1)
            
        t_str = target_date.strftime("%Y-%m-%d")
        p_str = prev_date.strftime("%Y-%m-%d")
        
        self.logger.info(f"Fetching Report Data: Target={t_str}, Prev={p_str}")
        
        # Coletar dados dos dois dias
        current_data = {}
        prev_data = {}
        
        # Buscar para todos os símbolos mapeados
        for variable_key, platts_symbol in self.SYMBOLS_MAPPING.items():
            # Current
            df_curr = self.fetch_symbol_data(platts_symbol, t_str)
            if not df_curr.empty:
                # Assume última linha é a mais recente
                row = df_curr.iloc[-1]
                current_data[variable_key] = {
                    "price": float(row.get("value", 0)),
                    "desc": row.get("symbol_description", row.get("description", "Unknown")),
                    "uom": row.get("uom", "")
                }
            
            # Previous
            df_prev = self.fetch_symbol_data(platts_symbol, p_str)
            if not df_prev.empty:
                row = df_prev.iloc[-1]
                prev_data[variable_key] = float(row.get("value", 0))
                
        if not current_data:
            self.logger.warning(f"No data found for target date {t_str}")
            return []
            
        # Montar lista final com cálculos
        items = []
        for key, curr in current_data.items():
            price = curr["price"]
            prev_price = prev_data.get(key, price) # Se não tiver anterior, change = 0
            
            change = price - prev_price
            pct_change = (change / prev_price * 100) if prev_price != 0 else 0.0
            
            items.append({
                "product": curr["desc"], # Nome vindo da API
                "variable_key": key,     # Chave interna para Whitelist manual se precisar
                "price": price,
                "unit": curr["uom"],
                "change": change,
                "changePercent": pct_change,
                "assessmentType": curr["uom"] # Usar unidade como tipo por enquanto
            })
            
        self.logger.info(f"Generated {len(items)} report items.")
        return items

