from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    gcp_project_id: str
    gcp_region: str
    gar_repository_name: str
    gcp_storage_bucket: str = "perd-fd33f.firebasestorage.app"




    class Config:
        env_file = ".env"

settings = Settings()
