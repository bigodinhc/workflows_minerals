
import os
import time
import requests
import json
from datetime import datetime
from execution.core.logger import WorkflowLogger

class ApifyClient:
    """
    Client for interacting with Apify API.
    Handles running actors and retrieving datasets.
    """
    
    BASE_URL = "https://api.apify.com/v2"
    
    def __init__(self, token=None):
        self.token = token or os.getenv("APIFY_API_TOKEN")
        if not self.token:
            raise ValueError("APIFY_API_TOKEN not found in environment")
        self.logger = WorkflowLogger("ApifyClient")
        
    def run_actor(self, actor_id, run_input, memory_mbytes=1024, timeout_secs=600):
        """
        Runs an Apify actor and waits for completion.
        Returns the dataset_id containing the results.
        """
        verify_ssl = True # Can be disabled for debugging if needed
        
        url = f"{self.BASE_URL}/acts/{actor_id}/runs"
        params = {
            "token": self.token,
            "memory": memory_mbytes,
            "timeout": timeout_secs
        }
        
        self.logger.info(f"Starting actor {actor_id}...")
        
        try:
            # Start the run
            res = requests.post(url, json=run_input, params=params, verify=verify_ssl)
            res.raise_for_status()
            data = res.json()["data"]
            run_id = data["id"]
            default_dataset_id = data["defaultDatasetId"]
            
            self.logger.info(f"Actor started. Run ID: {run_id}")
            
            # Poll for completion
            while True:
                time.sleep(5)
                run_status = self._get_run_status(run_id)
                status = run_status["status"]
                
                if status == "SUCCEEDED":
                    self.logger.info("Actor finished successfully.")
                    return default_dataset_id
                elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                    raise Exception(f"Actor run failed with status: {status}")
                else:
                    # RUNNING, READY, etc.
                    continue
                    
        except Exception as e:
            self.logger.error(f"Apify execution failed: {str(e)}")
            raise e
            
    def _get_run_status(self, run_id):
        url = f"{self.BASE_URL}/actor-runs/{run_id}"
        params = {"token": self.token}
        res = requests.get(url, params=params)
        res.raise_for_status()
        return res.json()["data"]
        
    def get_dataset_items(self, dataset_id):
        """
        Retrieves items from a dataset.
        """
        url = f"{self.BASE_URL}/datasets/{dataset_id}/items"
        params = {"token": self.token, "format": "json"}
        
        self.logger.info(f"Fetching dataset {dataset_id}...")
        res = requests.get(url, params=params)
        res.raise_for_status()
        
        items = res.json()
        self.logger.info(f"Retrieved {len(items)} items from dataset.")
        return items
