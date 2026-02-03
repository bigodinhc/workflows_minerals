import os
from supabase import create_client, Client
from ..core.logger import WorkflowLogger

class SupabaseClient:
    def __init__(self):
        url: str = os.environ.get("SUPABASE_URL")
        key: str = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in env")
        
        self.client: Client = create_client(url, key)
        self.logger = WorkflowLogger("SupabaseClient")

    def get_latest_prices(self, limit=12):
        """
        Fetch latest SGX Iron Ore prices.
        Assumes table 'sgx_prices' exists. 
        Adjust query as needed based on actual schema.
        """
        try:
            # TODO: CONFIRM TABLE NAME WITH USER
            # For now getting descending order by date
            response = self.client.table("sgx_prices") \
                .select("*") \
                .order("date", desc=True) \
                .limit(limit) \
                .execute()
                
            return response.data
        except Exception as e:
            self.logger.error("Failed to fetch prices from Supabase", {"error": str(e)})
            raise e
