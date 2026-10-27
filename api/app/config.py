from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str = "dev-secret-change-me"
    public_base_url: str = "http://localhost:8080"
    admin_user: str = "admin"
    admin_password: str = "admin"
    database_url: str = "sqlite:////tmp/uccx_portal.db"
    xml_dir: str = "/home/deepadm/uccx-portal/data/xml"
    access_token_expire_minutes: int = 480

    uccx_host: str = "172.27.200.7"
    uccx_user: str = ""
    uccx_password: str = ""
    uccx_verify_ssl: bool = False
    uccx_sync_enabled: bool = True
    uccx_document_folder: str = "DEFAULT"
    # Roll auto des fériés Y/En passés (secondes) ; 0 = désactivé
    holiday_roll_interval_sec: int = 3600

    # Piper TTS (POST /api/public/tts → WAV µ-law pour UCCX)
    tts_enabled: bool = True
    # Moteur par défaut API publique (surchargeable via data/portal_tts.json + param engine)
    tts_public_engine: str = "piper"
    piper_bin: str = "/usr/local/bin/piper"
    piper_model: str = "/data/piper/fr_FR-siwis-medium.onnx"
    # Mapping supplémentaire lang:model (CSV), ex. de_DE:de_DE-thorsten-medium
    piper_lang_map: str = ""
    piper_max_chars: int = 500
    tts_rate_limit_per_min: int = 30
    # IPs autorisées (UCCX + loopback tests). Traefik = proxy de confiance.
    tts_allowed_cidrs: str = "172.27.200.0/24,127.0.0.1/32,::1/128"
    # Réseau Docker Traefik → on lit X-Forwarded-For
    tts_trusted_proxy_cidrs: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32"

    # Kokoro TTS (UI portail — préécoute PCM)
    kokoro_enabled: bool = True
    kokoro_model: str = "/data/kokoro/kokoro-v1.0.int8.onnx"
    kokoro_voices: str = "/data/kokoro/voices-v1.0.bin"

    # ElevenLabs TTS (cloud — clé via .env ou portal_secrets)
    elevenlabs_enabled: bool = True
    elevenlabs_api_key: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"

    # Microsoft Edge TTS (edge-tts — gratuit, outbound HTTPS)
    edge_enabled: bool = True

    # Whisper STT (transcription prompts portail)
    stt_enabled: bool = True
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_model_dir: str = "/data/whisper"

    # Secrets UI (Fernet) — défaut = dérivé de SECRET_KEY
    settings_fernet_key: str = ""

    # SAML SSO (opérateurs) — aussi surchargeable via portal_secrets
    saml_enabled: bool = False
    saml_idp_metadata_url: str = ""
    saml_idp_metadata_xml: str = ""
    saml_sp_entity_id: str = ""
    saml_userid_attr: str = "uid"
    saml_want_assertions_signed: bool = True
    # Certificat/clé SP (PEM) pour signatures AuthnRequest optionnelles
    saml_sp_x509_cert: str = ""
    saml_sp_private_key: str = ""

    # Skills scheduler (secondes) ; 0 = désactivé
    skill_scheduler_interval_sec: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
