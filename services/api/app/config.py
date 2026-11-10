from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///./late_payer.db"
    web_origin: str = "http://localhost:3000"
    session_secret: str = "local-development-secret-change-me"
    operator_username: str = "operator"
    operator_password: str = "change-this-local-password"
    session_max_age_seconds: int = 43_200
    paypal_mode: str = "sandbox"
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_webhook_id: str = ""
    paypal_allowed_recipient_emails: str = ""
    paypal_sandbox_currency: str = "USD"
    merchant_timezone: str = "UTC"
    reminder_days_before_due: int = 3
    reminder_cooldown_days: int = 7
    friendly_payment_probability_threshold: float = 0.5
    gemini_api_key: str = ""
    model_path: str = "artifacts/payment_hazard.txt"
    feature_schema_path: str = "artifacts/feature_schema.json"

    @property
    def paypal_api_base(self) -> str:
        if self.paypal_mode.lower() == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    @property
    def allowed_recipients(self) -> set[str]:
        return {address.strip().lower() for address in self.paypal_allowed_recipient_emails.split(",") if address.strip()}

    @property
    def live_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def production_auth_configured(self) -> bool:
        if self.app_env.lower() != "production":
            return True
        return (
            len(self.session_secret) >= 32
            and self.session_secret != "local-development-secret-change-me"
            and self.operator_username != "operator"
            and self.operator_password != "change-this-local-password"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
