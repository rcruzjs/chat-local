from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    litellm_url: str = "http://gateway:4000"
    litellm_master_key: str
    embed_url: str = "http://embed:8081"
    rerank_url: str = "http://rerank:8082"
    llm_swap_url: str = "http://llm:8080"
    default_chat_model: str = "chat-4b"
    jwt_secret: str = "troque-este-segredo"
    jwt_horas: int = 12                 # duração da sessão
    rate_limit_por_minuto: int = 20     # perguntas por usuário por minuto
    seed_usuarios: bool = True          # cria usuario1..usuario10 na subida
    models_dir: str = "/models"         # para saber quais modelos estão baixados
    files_dir: str = "/data/files"


settings = Settings()
