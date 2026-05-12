from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str
    qf_client_id: str
    qf_client_secret: str
    qf_redirect_uri: str
    frontend_url: str
    cors_origins: str = "http://localhost:3000,https://al-hifz.vercel.app"
    qf_auth_base_url: str = "https://oauth2.quran.foundation"
    qf_content_base_url: str = "https://apis.quran.foundation/content/api/v4"
    qf_content_token_url: str = "https://oauth2.quran.foundation/oauth2/token"
    qf_user_api_base_url: str = "https://apis.quran.foundation"
    qf_audio_base_url: str = "https://verses.quran.foundation/"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()  # type: ignore[call-arg]
