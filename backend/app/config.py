from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://canvas_agent:change_me@localhost:5432/canvas_agent"

    canvas_base_url: str = "https://canvas.ualberta.ca"
    canvas_client_id: str = ""
    canvas_client_secret: str = ""
    canvas_redirect_uri: str = "http://localhost:8000/auth/canvas/callback"

    frontend_origin: str = "http://localhost:3000"
    session_secret: str = "change_me_to_a_random_string"
    session_cookie_name: str = "canvas_agent_session"
    # Local dev (plain HTTP, same-site localhost) needs secure=False and
    # samesite=lax. A real deployment over HTTPS with the frontend and
    # backend on different origins/subdomains needs secure=True and
    # samesite=none instead, or the browser silently drops the cookie -
    # set both via env vars, don't hardcode per-environment.
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"  # "lax" | "strict" | "none"

    # --- AWS Bedrock (urgency-ranking agent) ---
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "amazon.nova-micro-v1:0"


settings = Settings()
