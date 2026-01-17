from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    gcp_project_id: str
    gcp_region: str
    gar_repository_name: str
    gcp_storage_bucket: str = "" # Optional, can default to {project_id}_cloudbuild


    class Config:
        env_file = ".env"

settings = Settings()
