import os
import json
import pandas as pd
import lseg.data as ld
from datetime import date
from dateutil.relativedelta import relativedelta
from pathlib import Path
from ..core.logger import WorkflowLogger

class LSEGClient:
    def __init__(self):
        self.logger = WorkflowLogger("LSEGClient")
        self.config_path = self._create_config_file()
        
    def _create_config_file(self):
        """Creates a temporary config file for LSEG session."""
        app_key = os.environ.get("LSEG_APP_KEY")
        username = os.environ.get("LSEG_USERNAME")
        password = os.environ.get("LSEG_PASSWORD")
        
        # If running locally without envs, maybe file exists?
        # But for GH Action we definitely need envs.
        if not all([app_key, username, password]):
            # If no envs, check if default config exists (local dev)
            default_path = Path("lseg-data.config.json")
            if default_path.exists():
                return default_path
            self.logger.warning("LSEG credentials missing in env, will try default resolution but likely fail.")
            
        config_content = {
            "sessions": {
                "default": "platform.ldp",
                "platform": {
                    "ldp": {
                        "app-key": app_key,
                        "username": username,
                        "password": password,
                        "signon_control": True
                    }
                }
            }
        }
        
        path = Path(".tmp/lseg-config.json")
        path.parent.mkdir(exist_ok=True)
        with open(path, "w") as f:
            json.dump(config_content, f)
        return path

    def connect(self):
        try:
            self.logger.info(f"Connecting to LSEG Platform using {self.config_path}...")
            ld.open_session(config_name=str(self.config_path))
            self.logger.info("Connected to LSEG.")
        except Exception as e:
            self.logger.error("Failed to connect to LSEG", {"error": str(e)})
            raise e

    def close(self):
        try:
            ld.close_session()
            self.logger.info("LSEG session closed.")
        except:
            pass

    def get_futures_data(self):
        """
        Fetches snapshot of SGX Iron Ore futures 1-12 months.
        Returns list of dicts: { month, price, change, pct_change }
        """
        MAX_CONTRACTS = 12
        MONTH_CODES = {1:"F", 2:"G", 3:"H", 4:"J", 5:"K", 6:"M", 7:"N", 8:"Q", 9:"U", 10:"V", 11:"X", 12:"Z"}
        FIELDS = ["TRDPRC_1", "SETTLE", "NETCHNG_1", "PCTCHNG", "EXPIR_DATE"]
        
        # Generate RICs
        rics = []
        today = date.today()
        for i in range(MAX_CONTRACTS):
            target_date = today + relativedelta(months=i)
            # Logic from original script: Year code is last digit
            year_code = str(target_date.year)[-1]
            month_code = MONTH_CODES[target_date.month]
            rics.append(f"SZZF{month_code}{year_code}")
            
        self.logger.info(f"Fetching data for: {rics}")
        
        try:
            df = ld.get_data(universe=rics, fields=FIELDS)
            
            if df is None or df.empty:
                self.logger.warning("No data returned from LSEG")
                return []
            
            # Handle LSEG DataFrame weirdness (sometimes Instrument is index)
            if df.index.name == 'Instrument':
                df = df.reset_index()
            
            results = []
            for _, row in df.iterrows():
                # Extract Price (prioritize Trade Price, then Settle)
                price = row.get("TRDPRC_1")
                if pd.isna(price):
                    price = row.get("SETTLE")
                
                if pd.isna(price):
                    continue
                    
                # Extract columns safely
                change = row.get("NETCHNG_1", 0.0)
                if pd.isna(change): change = 0.0
                
                pct = row.get("PCTCHNG", 0.0)
                if pd.isna(pct): pct = 0.0
                
                expiry = row.get("EXPIR_DATE")
                
                # Format month string Mmm/YY (e.g. Sep/25)
                month_str = "???"
                if pd.notna(expiry):
                    exp_dt = pd.to_datetime(expiry)
                    month_str = exp_dt.strftime("%b/%y").upper()
                    
                results.append({
                    "month": month_str,
                    "price": float(price),
                    "change": float(change),
                    "pct_change": float(pct)
                })
                
            # Sort by expiration (naive sort by list order is usually fine if returned in order, 
            # but LSEG might shuffle. Since we generated RICs in order, we can map back if needed.
            # For now, assuming list order or simple date parse if critical.)
            
            return results
            
        except Exception as e:
            self.logger.error("Error in get_futures_data", {"error": str(e)})
            raise e
