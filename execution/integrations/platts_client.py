
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
    SYMBOLS_MAPPING = {
        "PLATTS_IODEX_62_CFR_CHINA": "IODBZ00",
        "PLATTS_VIU_FE_60_63": "IOMGD00",
        "PLATTS_SILICA_3_4P5": "IOALF00",
        "PLATTS_SILICA_4P5_6P5": "IOPPS10",
        "PLATTS_SILICA_6P5_9": "IOPPS20",
        "PLATTS_ALUMINA_1_2P5": "IOADF10",
        "PLATTS_ALUMINA_2P5_4": "IOALE00",
        "PLATTS_P_PENALTY": "IOPPQ00",
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

