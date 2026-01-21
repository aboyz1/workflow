import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    gcp_project_id = os.getenv("GCP_PROJECT_ID")
    gcp_region = os.getenv("GCP_REGION")
    gar_repository_name = os.getenv("GAR_REPOSITORY_NAME")
    gcp_storage_bucket = os.getenv("GCP_STORAGE_BUCKET", "perd-fd33f.firebasestorage.app")

settings = Settings()
