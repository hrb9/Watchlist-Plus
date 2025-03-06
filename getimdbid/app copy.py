from flask import Flask, request, jsonify
import logging
import requests
from tmdb_services import get_imdb_id as tmdb_get_imdb_id
from imdbmovies import IMDB

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

if __name__ == '__main__':
    # Host=0.0.0.0 so that it is accessible from other containers (not just localhost).
    app.run(host='0.0.0.0', port=5331, debug=True)
