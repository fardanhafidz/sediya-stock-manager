from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str
    DATABASE_URL_SYNC: str

    # Evolution API (WhatsApp)
    EVOLUTION_API_URL: str
    EVOLUTION_API_KEY: str
    EVOLUTION_INSTANCE: str

    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT: str
    AZURE_OPENAI_KEY: str
    AZURE_OPENAI_DEPLOYMENT: str
    OPENAI_API_VERSION: str

    # App Settings
    APP_PORT: int

    # Otomatis baca dari file .env
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# Kita buat satu instance biar bisa di-import di mana saja
settings = Settings()
