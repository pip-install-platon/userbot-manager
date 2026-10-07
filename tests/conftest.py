import base64
import os

os.environ["POSTGRES_USER"] = "userbot"
os.environ["POSTGRES_PASSWORD"] = "test"
os.environ["POSTGRES_DB"] = "userbot"
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/15"
os.environ["MASTER_KEY"] = base64.b64encode(b"k" * 32).decode()
os.environ["API_ID"] = "1"
os.environ["API_HASH"] = "hash"
os.environ["SESSION_STRING"] = "session"
os.environ["BOT_TOKEN"] = "123:token"
os.environ["AUTO_ASSIGN"] = "true"
os.environ["LOG_LEVEL"] = "INFO"
os.environ.pop("SUPERADMIN_TELEGRAM_ID", None)
