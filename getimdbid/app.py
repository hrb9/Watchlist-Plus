from flask import Flask, request, jsonify
import logging
import requests
from tmdb_services import get_imdb_id as tmdb_get_imdb_id
from imdbmovies import IMDB
import os   

app = Flask(__name__)
imdb = IMDB()

def extract_guid_id(guid_id, prefix):
    """Extract ID from guid with given prefix"""
    if guid_id.startswith(prefix):
        return guid_id.split(prefix)[1]
    return None

def get_imdb_from_title(title, media_type):
    """Search IMDB by title"""
    try:
        result = imdb.get_by_name(title, tv=(media_type in ['show', 'episode']))
        if result and 'url' in result:
            return result['url'].split("https://www.imdb.com/title/")[1].split("/")[0]
    except Exception as e:
        logging.error(f"Error in IMDB lookup for {title}: {e}")
    return None

@app.route('/getimdbid', methods=['POST'])
def get_imdb_id():
    data = request.json
    title = data.get('title')
    media_type = data.get('type')
    guids = data.get('guids', [])
    
    # Try getting IMDB ID directly from guids
    for guid in guids:
        imdb_id = extract_guid_id(guid, "imdb://")
        if imdb_id:
            return jsonify({"imdb_id": imdb_id})
    
    # Try converting from TMDB ID
    for guid in guids:
        tmdb_id = extract_guid_id(guid, "tmdb://")
        if tmdb_id:
            imdb_id = tmdb_get_imdb_id(tmdb_id, "movie" if media_type == 'movie' else "tv")
            if imdb_id:
                return jsonify({"imdb_id": imdb_id})
    
    # Try searching by title as last resort
    if title:
        imdb_id = get_imdb_from_title(title, media_type)
        if imdb_id:
            return jsonify({"imdb_id": imdb_id})
    
    # Generate synthetic ID if all else fails
    title_hash = int(''.join(str(ord(c)) for c in title.replace(' ', '_'))) % 10000000
    synthetic_id = f"tt{title_hash}1990"
    
    return jsonify({"imdb_id": synthetic_id})

@app.route('/convert_ids', methods=['POST'])
def convert_ids():
    """
    Convert between different media IDs (IMDb, TMDb, TVDb) and get title and Overseerr media ID.
    
    Request format:
    {
        "imdb_id": "tt12345678",  // Optional: one of the IDs must be provided
        "tmdb_id": 12345,         // Optional
        "tvdb_id": 12345,         // Optional
        "media_type": "movie"     // Required: "movie" or "tv"
    }
    
    Response format:
    {
        "imdb_id": "tt12345678",
        "tmdb_id": 12345,
        "tvdb_id": 12345,
        "title": "Movie Title",
        "overseerr_id": 12345,
        "media_type": "movie"
    }
    """
    try:
        # Import what we need from tmdb_services with fallbacks
        import tmdb_services
        
        # Check what functions are available in tmdb_services and log
        available_functions = [f for f in dir(tmdb_services) if callable(getattr(tmdb_services, f)) and not f.startswith('_')]
        logging.info(f"Available functions in tmdb_services: {available_functions}")
        
        # Get the functions we need with safe fallbacks
        get_tmdb_id = getattr(tmdb_services, 'get_tmdb_id', lambda x: None)
        get_imdb_id = getattr(tmdb_services, 'get_imdb_id', lambda x, y: None)
        get_tvdb_id = getattr(tmdb_services, 'get_tvdb_id', lambda x: None)
        get_movie_details = getattr(tmdb_services, 'get_movie_details', lambda x: {'title': None})
        get_tv_details = getattr(tmdb_services, 'get_tv_details', lambda x: {'name': None})
        
        data = request.json
        logging.info(f"Request data: {data}")
        imdb_id = data.get('imdb_id')
        tmdb_id = data.get('tmdb_id')
        tvdb_id = data.get('tvdb_id')
        media_type = data.get('media_type', 'movie')
        
        result = {
            'imdb_id': imdb_id,
            'tmdb_id': tmdb_id,
            'tvdb_id': tvdb_id,
            'title': None,
            'overseerr_id': None,
            'media_type': media_type
        }
        
        # Step 1: Get TMDb ID if we don't have it yet
        if not tmdb_id and imdb_id:
            try:
                tmdb_id = get_tmdb_id(imdb_id)
                result['tmdb_id'] = tmdb_id
                logging.info(f"Converted imdb_id {imdb_id} to tmdb_id {tmdb_id}")
            except Exception as e:
                logging.error(f"Error getting tmdb_id from imdb_id {imdb_id}: {e}")
        
        # Step 2: Get details based on TMDb ID
        if tmdb_id:
            try:
                if media_type == 'movie':
                    details = get_movie_details(tmdb_id)
                    if details:
                        result['title'] = details.get('title')
                else:
                    details = get_tv_details(tmdb_id)
                    if details:
                        result['title'] = details.get('name')
                logging.info(f"Got title '{result['title']}' for tmdb_id {tmdb_id}")
            except Exception as e:
                logging.error(f"Error getting details for tmdb_id {tmdb_id}: {e}")
                    
            # Step 3: Get IMDb ID if we don't have it yet
            if not imdb_id:
                try:
                    imdb_id = get_imdb_id(tmdb_id, media_type)
                    result['imdb_id'] = imdb_id
                    logging.info(f"Converted tmdb_id {tmdb_id} to imdb_id {imdb_id}")
                except Exception as e:
                    logging.error(f"Error getting imdb_id from tmdb_id {tmdb_id}: {e}")
                
            # Step 4: Get TVDb ID if we don't have it yet
            if not tvdb_id and media_type == 'tv':
                try:
                    tvdb_id = get_tvdb_id(tmdb_id)
                    result['tvdb_id'] = tvdb_id
                    logging.info(f"Converted tmdb_id {tmdb_id} to tvdb_id {tvdb_id}")
                except Exception as e:
                    logging.error(f"Error getting tvdb_id from tmdb_id {tmdb_id}: {e}")
        
        # Step 5: Look up Overseerr ID if we have a title
        if result['title']:
            try:
                overseerr_id = get_overseerr_id(result['title'], media_type, result['tvdb_id'])
                result['overseerr_id'] = overseerr_id
                logging.info(f"Got overseerr_id {overseerr_id} for title '{result['title']}'")
            except Exception as e:
                logging.error(f"Error getting overseerr_id for title '{result['title']}': {e}")
        
        logging.info(f"Final result: {result}")
        return jsonify(result)
    except Exception as e:
        logging.error(f"Unhandled exception in convert_ids: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

def get_overseerr_id(title, media_type, tvdb_id=None):
    """
    Search Overseerr by title and get media ID.
    Similar to what request_media_from_overseer does in watchlistrequests.
    """
    from urllib.parse import quote
    
    overseerr_url = os.environ.get("OVERSEERR_URL", "http://localhost:5055")
    overseerr_api_key = os.environ.get("OVERSEERR_API_KEY")
    
    if not overseerr_api_key or not title:
        return None
    
    # Setup headers with the API key
    headers = {
        "accept": "application/json",
        "X-Api-Key": overseerr_api_key
    }

    # Search Overseerr by media title with URL encoding
    search_endpoint = f"{overseerr_url}/api/v1/search"
    encoded_title = quote(title)
    params = {"query": encoded_title}
    
    try:
        search_response = requests.get(search_endpoint, params=params, headers=headers)
        search_response.raise_for_status()
        search_results = search_response.json().get("results", [])
        
        # Find the matching media in Overseerr
        matched_media = None
        for item in search_results:
            if media_type == "tv" and tvdb_id:
                # Compare tvdbId for TV shows
                if item.get("tvdbId") == tvdb_id:
                    matched_media = item
                    break
            else:
                # For movies or if no tvdb_id, compare titles (case-insensitive)
                if item.get("title", "").lower() == title.lower():
                    matched_media = item
                    break
        
        # If no exact match found, use the first result if available
        if not matched_media and search_results:
            matched_media = search_results[0]
        
        if matched_media:
            return matched_media.get("id")
            
    except Exception as e:
        logging.error(f"Error searching Overseerr for title '{title}': {e}")
        
    return None

if __name__ == '__main__':
    # Host=0.0.0.0 so that it is accessible from other containers (not just localhost).
    app.run(host='0.0.0.0', port=5331, debug=True)
