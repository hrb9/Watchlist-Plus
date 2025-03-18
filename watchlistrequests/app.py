def request_media_from_overseer(imdb_id, media_type="movie", title=None):
    """
    Converts an IMDb ID to media details using TMDb API, searches Overseerr by title,
    compares the tvdbId (if media_type is "tv") and sends a request to Overseerr.
    
    Uses the new getimdbid service endpoint for ID conversion and Overseerr searching.
    
    Args:
        imdb_id (str): The IMDb ID of the media.
        media_type (str): "movie" for movies or "tv" for TV shows (default: "movie").
        title (str, optional): The title of the media, if known.
        
    Returns:
        dict: The JSON response from Overseerr or error details.
    """
    overseerr_url = os.environ.get("OVERSEERR_URL", "http://localhost:5055")
    overseerr_api_key = os.environ.get("OVERSEERR_API_KEY")
    getimdbid_url = os.environ.get("GETIMDBID_URL", "http://getimdbid:5331")
    
    if not overseerr_api_key:
        error_msg = "OVERSEERR_API_KEY not set in environment variables."
        logging.error(error_msg)
        return {"error": error_msg}
    
    # Setup headers with the API key
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "X-Api-Key": overseerr_api_key
    }

    # Step 1: Convert IMDb ID to all media details using the new endpoint
    try:
        # Include title in the request payload if available
        payload = {
            "imdb_id": imdb_id, 
            "media_type": media_type
        }
        if title:
            payload["title"] = title
            logging.info(f"Including title '{title}' in convert_ids request")
            
        r = requests.post(f"{getimdbid_url}/convert_ids", json=payload)
        r.raise_for_status()
        media_details = r.json()
        
        # Use the provided title if no title returned from the API
        title = media_details.get("title") or title
        tvdbId = media_details.get("tvdb_id", 0)
        overseerr_id = media_details.get("overseerr_id")
        
        if not overseerr_id:
            logging.warning(f"Could not find Overseerr ID for IMDb ID: {imdb_id}, trying alternative media type")
            # Try the opposite media type as a fallback
            alt_media_type = "tv" if media_type == "movie" else "movie"
            r = requests.post(f"{getimdbid_url}/convert_ids", 
                            json={"imdb_id": imdb_id, "media_type": alt_media_type, "title": title})
            r.raise_for_status()
            media_details = r.json()
            title = media_details.get("title") or title  # Keep original title if no new one
            tvdbId = media_details.get("tvdb_id", 0)
            overseerr_id = media_details.get("overseerr_id")
            
            if not overseerr_id:
                error_msg = f"Could not find Overseerr ID for IMDb ID: {imdb_id} with either media type"
                logging.error(error_msg)
                return {"error": error_msg}
                
            # Update media_type to the one that worked
            media_type = alt_media_type
            logging.info(f"Successfully found with alternative media type: {alt_media_type}")
        
        # Step 2: Get seasons if it's a TV show
        seasons = []
        if media_type.lower() == "tv":
            try:
                # Get the series details to find available seasons
                series_endpoint = f"{overseerr_url}/api/v1/tv/{overseerr_id}"
                series_response = requests.get(series_endpoint, headers=headers)
                series_response.raise_for_status()
                series_data = series_response.json()
                
                # Extract season numbers (excluding specials season 0)
                seasons = [season['seasonNumber'] for season in series_data.get('seasons', []) 
                          if season.get('seasonNumber', 0) > 0]
                
                logging.info(f"Found {len(seasons)} seasons for '{title}': {seasons}")
                
                # If no seasons found or error, default to requesting all seasons with empty array
                if not seasons:
                    logging.warning(f"No specific seasons found for '{title}', requesting all seasons")
            except Exception as e:
                logging.warning(f"Error getting seasons for '{title}': {e}, requesting all seasons")
        
        # Build payload for Overseerr request
        payload = {
            "mediaType": media_type,          # "movie" or "tv"
            "mediaId": overseerr_id,          # Overseerr's internal media ID
            "tvdbId": tvdbId,                 # TVDB ID (0 for movies)
            "seasons": seasons,               # All seasons for TV shows, empty for movies
            "is4k": False,
            "serverId": 0,
            "profileId": 0,
            "rootFolder": "",                 # Empty string instead of "string"
            "languageProfileId": 0,
            "userId": 0
        }
        
        # Send request to Overseerr
        request_endpoint = f"{overseerr_url}/api/v1/request"
        logging.info(f"Sending request to Overseerr for '{title}' ({media_type}) with payload: {payload}")
        request_response = requests.post(request_endpoint, json=payload, headers=headers)
        request_response.raise_for_status()
        logging.info(f"Successfully sent request for media '{title}' (Overseerr mediaId: {overseerr_id})")
        return request_response.json()
        
    except Exception as e:
        error_msg = f"Error processing request for IMDb ID {imdb_id}: {e}"
        logging.error(error_msg)
        return {"error": error_msg}