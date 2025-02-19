# watchlistdispatcher/config.py
import os

# Configuration for Radarr/Sonarr servers
DEFAULT_SERVER_URL = os.environ.get("DEFAULT_SERVER_URL", "http://default-server:7878")
DEFAULT_SERVER_TOKEN = os.environ.get("DEFAULT_SERVER_TOKEN", "default_token")

SERVER1_URL = os.environ.get("SERVER1_URL", "http://server1:7878")
SERVER1_TOKEN = os.environ.get("SERVER1_TOKEN", "server1_token")

SERVER2_URL = os.environ.get("SERVER2_URL", "http://server2:7878")
SERVER2_TOKEN = os.environ.get("SERVER2_TOKEN", "server2_token")

OVERSEERR_API_TOKEN = os.environ.get("OVERSEERR_API_TOKEN", "your_overseerr_api_token_here")
OVERSEERR_URL = os.environ.get("OVERSEERR_URL", "http://overseerr:5055")
