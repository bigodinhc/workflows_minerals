
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
    # Mapeamento Detalhado (Symbol -> Description)
    # Isso garante que sempre teremos o nome correto, mesmo que a API não retorne.
    SYMBOLS_DETAILS = {
        # --- FINES ---
        "IOBBA00": "Brazilian Blend Fines CFR Qingdao $/DMT",
        "IODFE00": "IO fines Fe 58% $/DMt",
        "IOPRM00": "IO fines Fe 65% $/DMt",
        "IOJBA00": "Jimblebar Fines CFR Qingdao $/DMT",
        "IOMAA00": "Mining Area C Fines CFR Qingdao $/DMT",
        "IONHA00": "Newman High Grade Fines CFR Qingdao $/DMT",
        "IOPBQ00": "Pilbara Blend Fines CFR Qingdao $/DMT",
        "IODBZ00": "IODEX CFR CHINA 62% Fe $/DMt",
        "TS01021": "TSI Iron Ore Fines 62% Fe CFR China",

        # --- LUMP & PELLET ---
        "IODRP00": "Iron Ore 67.5% Fe DR Pellet Premium (62% Fe basis) $/DMT Mthly",
        "IOCQR04": "Iron Ore Blast Furnace 63% Fe Pellet CFR China $/DMT",
        "IOBFC04": "Iron Ore Blast Furnace Pellet Premium CFR China $/DMT Wkly",
        "IOCLS00": "Iron Ore Lump Outright Price CFR China",

        # --- VIU DIFFERENTIALS ---
        "IOALE00": "Iron Ore Alumina Differential per 1% with 2.5-4% $/DMT",
        "TSIAF00": "Iron Ore Alumina Differential per 1% within <5% (55-60% Fe Fines)",
        "TSIAD00": "Iron Ore Fe Differential per 1% Fe within 55-60% Fe Fines",
        "IOPPQ00": "Iron Ore Phosphorus Differential per 0.01% for 0.09%-0.12% $/DMT",
        "IOPPT00": "Iron Ore Phosphorus Differential per 0.01% for 0.10%-0.11% $/DMT",
        "IOPPU00": "Iron Ore Phosphorus Differential per 0.01% for 0.11%-0.12% $/DMT",
        "IOPPV00": "Iron Ore Phosphorus Differential per 0.01% for 0.12%-0.15% $/DMT",
        "IOALF00": "Iron Ore Silica Differential per 1% with 3-4.5% range $/DMT",
        "TSIAI00": "Iron Ore Silica Differential per 1% within 55-60% Fe Fines",
        "IOADF10": "Iron ore Alumina differential per 1% within 1-2.5% $/DMT",
        "IOPPS10": "Iron ore Silica differential per 1% within 4.5-6.5% $/DMT",
        "IOPPS20": "Iron ore Silica differential per 1% within 6.5-9% $/DMT",
        "IOMGD00": "Mid Range Diff 60-63.5 Fe $/DMt",
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
        self.logger.info(f"Total symbols to fetch: {len(self.SYMBOLS_DETAILS)}")
        
        # Coletar dados dos dois dias
        current_data = {}
        prev_data = {}
        
        # Track success/failure per symbol
        success_symbols = []
        failed_symbols = []
        
        # Buscar para todos os símbolos mapeados
        for symbol, description in self.SYMBOLS_DETAILS.items():
            # Current
            df_curr = self.fetch_symbol_data(symbol, t_str)
            if not df_curr.empty:
                # Assume última linha é a mais recente
                row = df_curr.iloc[-1]
                
                current_data[symbol] = {
                    "price": float(row.get("value", 0)),
                    "desc": description,
                    "uom": row.get("uom", "")
                }
                success_symbols.append(symbol)
                self.logger.info(f"  ✓ {symbol}: {description[:40]}... = ${row.get('value', 0)}")
            else:
                failed_symbols.append(symbol)
                self.logger.warning(f"  ✗ {symbol}: {description[:40]}... (no data)")
            
            # Previous (silent - only for change calculation)
            df_prev = self.fetch_symbol_data(symbol, p_str)
            if not df_prev.empty:
                row = df_prev.iloc[-1]
                prev_data[symbol] = float(row.get("value", 0))
        
        # Summary logging
        total = len(self.SYMBOLS_DETAILS)
        self.logger.info(f"=== COLLECTION SUMMARY ===")
        self.logger.info(f"  ✓ Success: {len(success_symbols)}/{total}")
        self.logger.info(f"  ✗ Failed:  {len(failed_symbols)}/{total}")
        
        if failed_symbols:
            self.logger.warning(f"  Missing symbols: {', '.join(failed_symbols)}")
                
        if not current_data:
            self.logger.warning(f"No data found for target date {t_str}")
            return []
            
        # Montar lista final com cálculos
        self.logger.info("--- COLLECTED ITEM LOGS ---")
        items = []
        for key, curr in current_data.items():
            price = curr["price"]
            prev_price = prev_data.get(key, price) # Se não tiver anterior, change = 0
            
            change = price - prev_price
            pct_change = (change / prev_price * 100) if prev_price != 0 else 0.0
            
            # Log solicitado pelo usuário
            log_msg = f"Item: {curr['desc']} | Price: {price} | Change: {change:.2f} ({pct_change:.2f}%)"
            print(log_msg) # Print vai para o stdout do Action
            # self.logger.info(log_msg) # Logger também guarda
            
            items.append({
                "product": curr["desc"],
                "variable_key": key, # Key is the Symbol itself now
                "price": price,
                "unit": curr["uom"],
                "change": change,
                "changePercent": pct_change,
                "assessmentType": curr["uom"]
            })
            
        self.logger.info(f"Generated {len(items)} report items.")
        return items

