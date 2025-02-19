# recbyhistory/config.py
import os

# API Keys and Overseerr configuration
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "your_tmdb_api_key_here")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "your_gemini_api_key_here")
OVERSEERR_API_TOKEN = os.environ.get("OVERSEERR_API_TOKEN", "your_overseerr_api_token_here")
OVERSEERR_URL = os.environ.get("OVERSEERR_URL", "http://localhost:5055")

# Database and application configuration
DB_FOLDER = os.environ.get("DB_FOLDER", "db")
ITEMS_PER_GROUP = int(os.environ.get("ITEMS_PER_GROUP", "5000"))
