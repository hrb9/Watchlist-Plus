import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config import TMDB_API_KEY

# Create a session with retry strategy
session = requests.Session()
retry_strategy = Retry(
    total=3,  # Number of retries
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS"]  # Change method_whitelist to allowed_methods
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

def get_tmdb_id(imdb_id):
    url = f"https://api.themoviedb.org/3/find/{imdb_id}"
    params = {
        "api_key": TMDB_API_KEY,
        "external_source": "imdb_id"
    }
    try:
        response = session.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        if 'movie_results' in data and data['movie_results']:
            return data['movie_results'][0]['id']
        elif 'tv_results' in data and data['tv_results']:
            return data['tv_results'][0]['id']
        else:
            print(f"TMDB ID not found for IMDb ID: {imdb_id}")
            return None
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            print(f"TMDB ID not found for IMDb ID: {imdb_id} - {e}")
        else:
            print(f"Error fetching TMDB ID for IMDb ID {imdb_id}: {e}")
        return None

def get_recommendations(tmdb_id, media_type, num_recommendations=200):
    url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/recommendations"
    params = {
        'api_key': TMDB_API_KEY,
        'language': 'en-US',
        'page': 1
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        recommendations = response.json().get('results', [])[:num_recommendations]
        return recommendations
    else:
        return []
 

def get_movie_details(tmdb_id):
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
    params = {"api_key": TMDB_API_KEY}
    try:
        response = session.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            print(f"Movie details not found for TMDB ID: {tmdb_id} - {e}")
        else:
            print(f"Error fetching movie details for TMDB ID {tmdb_id}: {e}")
        return {}

def get_tv_details(tmdb_id):
    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}"
    params = {"api_key": TMDB_API_KEY}
    try:
        response = session.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            print(f"TV details not found for TMDB ID: {tmdb_id} - {e}")
        else:
            print(f"Error fetching TV details for TMDB ID {tmdb_id}: {e}")
        return {}

def get_imdb_id(tmdb_id, media_type):
    url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/external_ids"
    params = {"api_key": TMDB_API_KEY}
    try:
        response = session.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        return data.get('imdb_id')
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            print(f"IMDb ID not found for TMDB ID: {tmdb_id} - {e}")
        else:
            print(f"Error fetching IMDb ID for TMDB ID {tmdb_id}: {e}")
        return None

def get_tvdb_id(tmdb_id):
    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/external_ids"
    params = {"api_key": TMDB_API_KEY}
    try:
        response = session.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        return data.get('tvdb_id')
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            print(f"TVDB ID not found for TMDB ID: {tmdb_id} - {e}")
        else:
            print(f"Error fetching TVDB ID for TMDB ID {tmdb_id}: {e}")
        return None

def get_tmdb_id_by_title_and_year(title, year, media_type):
    """Retrieves the TMDB ID based on title and year."""
    url = f"https://api.themoviedb.org/3/search/{media_type}"
    params = {
        'api_key': TMDB_API_KEY,
        'query': title,
        'year': year, 
        # 'include_adult': False  # Optional, adjust as needed
    }

    try:
        response = session.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if data['results']:
            return data['results'][0]['id']
        else:
            print(f"TMDB ID not found for {media_type}: {title} ({year})")
            return None
    except requests.exceptions.HTTPError as e:
        print(f"Error fetching TMDB ID for {media_type} {title} ({year}): {e}")
        return None
    