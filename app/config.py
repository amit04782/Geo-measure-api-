from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="GEO_")

    database_url: str = "sqlite:///./data/app.db"
    max_upload_mb: int = 50
    # Guards against zip bombs: max total uncompressed size of a shapefile zip.
    max_unzipped_mb: int = 500
    max_zip_members: int = 200


settings = Settings()
