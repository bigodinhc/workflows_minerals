
import os
from apify_client import ApifyClient as OfficialApifyClient
from execution.core.logger import WorkflowLogger

class ApifyClient:
    """
    Client for interacting with Apify API using the official SDK.
    Handles running actors and retrieving datasets.
    """
    
    def __init__(self, token=None):
        self.token = token or os.getenv("APIFY_API_TOKEN")
        if not self.token:
            raise ValueError("APIFY_API_TOKEN not found in environment")
        self.client = OfficialApifyClient(self.token)
        self.logger = WorkflowLogger("ApifyClient")
        
    def run_actor(self, actor_id, run_input, memory_mbytes=1024, timeout_secs=600):
        """
        Runs an Apify actor and waits for completion.
        Returns the dataset_id containing the results.
        """
        self.logger.info(f"Starting actor {actor_id}...")
        
        try:
            # Get actor client
            actor = self.client.actor(actor_id)
            
            # Call actor and wait for completion
            run = actor.call(
                run_input=run_input,
                memory_mbytes=memory_mbytes,
                timeout_secs=timeout_secs
            )
            
            run_id = run.get("id")
            default_dataset_id = run.get("defaultDatasetId")
            status = run.get("status")
            
            self.logger.info(f"Actor finished. Run ID: {run_id}, Status: {status}")
            
            if status != "SUCCEEDED":
                raise Exception(f"Actor run failed with status: {status}")
            
            return default_dataset_id
                    
        except Exception as e:
            self.logger.error(f"Apify execution failed: {str(e)}")
            raise e
        
    def get_dataset_items(self, dataset_id):
        """
        Retrieves items from a dataset.
        """
        self.logger.info(f"Fetching dataset {dataset_id}...")
        
        dataset = self.client.dataset(dataset_id)
        items = list(dataset.iterate_items())
        
        self.logger.info(f"Retrieved {len(items)} items from dataset.")
        return items
