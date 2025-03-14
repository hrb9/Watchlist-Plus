from flask import Flask, request, jsonify
import logging
import requests
from tmdb_services import get_imdb_id as tmdb_get_imdb_id
from imdbmovies import IMDB
import os   

logging.basicConfig(
    level=logging.DEBUG,  # Change to DEBUG for more verbose logs
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

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
    """Convert between different media IDs and get Overseerr ID"""
    try:
        # Import functions from tmdb_services
        from tmdb_services import get_tmdb_id, get_imdb_id, get_tvdb_id, get_movie_details, get_tv_details
        
        logging.info("Starting ID conversion process")
        
        data = request.json
        logging.info(f"Request data: {data}")
        imdb_id = data.get('imdb_id')
        tmdb_id = data.get('tmdb_id')
        tvdb_id = data.get('tvdb_id')
        media_type = data.get('media_type', 'movie')
        title = data.get('title')
        
        result = {
            'imdb_id': imdb_id,
            'tmdb_id': tmdb_id,
            'tvdb_id': tvdb_id,
            'title': title,
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
                # Use TMDb ID as Overseerr ID which is generally the same
                result['overseerr_id'] = int(tmdb_id) if tmdb_id else None
                
                # Or, if you need to actually look it up
                if not result['overseerr_id']:
                    overseerr_id = get_overseerr_id(result['title'], media_type, result['tvdb_id'])
                    result['overseerr_id'] = overseerr_id
                
                logging.info(f"Using overseerr_id {result['overseerr_id']} for title '{result['title']}'")
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
        logging.error("Missing Overseerr API key or title")
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
        response = requests.get(search_endpoint, params=params, headers=headers)
        response.raise_for_status()
        
        search_results = response.json().get("results", [])
        if not search_results:
            logging.warning(f"No search results found for title: {title}")
            return None
            
        # Find the correct result based on title and media type
        for result in search_results:
            if result.get("mediaType") == media_type:
                media_title = result.get("title") if media_type == "movie" else result.get("name")
                if media_title and media_title.lower() == title.lower():
                    # If it's a TV show and we have a TVDB ID, verify it matches
                    if media_type == "tv" and tvdb_id and result.get("tvdbId") and int(result.get("tvdbId")) != int(tvdb_id):
                        continue
                    
                    return result.get("id")
                
        # If no exact match, return the first result of the correct media type
        for result in search_results:
            if result.get("mediaType") == media_type:
                return result.get("id")
                
        return None
    except Exception as e:
        logging.error(f"Error searching Overseerr for {title}: {e}")
        return None

if __name__ == '__main__':
    # Host=0.0.0.0 so that it is accessible from other containers (not just localhost).
    app.run(host='0.0.0.0', port=5331, debug=True)
