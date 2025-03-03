# recbyhistory/rec.py
import os
import time
import json
import re
import requests
import tiktoken
from google import genai
from google.genai.types import Tool, GenerateContentConfig, GoogleSearch
from db import Database
from config import ITEMS_PER_GROUP, OVERSEERR_URL, OVERSEERR_API_TOKEN

# Control variables
NUM_MOVIES = 3
NUM_SERIES = 2
RATING_THRESHOLD = 5.0

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

def init_gemini_client():
    """Initialize Gemini client with API key"""
    try:
        return genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
    except Exception as e:
        print(f"Error initializing Gemini client: {e}")
        return None

# Initialize tools
google_search_tool = Tool(google_search=GoogleSearch())

def format_history_for_ai(group):
    prompt = ""
    for item in group:
        rating = item[3] if item[3] is not None else "N/A"
        prompt += f"Watch History - Title: {item[1]}, IMDB ID: {item[2]}, User Rating: {rating}\n"
    return prompt

def get_user_taste(all_groups_history):
    if not all_groups_history.strip():
        return "No watch history available to determine user taste."
    
    client = init_gemini_client()
    if not client:
        return "Error initializing AI client."
        
    system_instruction = (
        "Analyze the following watch history and provide a detailed, authentic description of the user's taste in films and TV shows. "
        "Include preferred genres, styles, directors, and unique characteristics. Return only the detailed description."
    )
    config = GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=1,
        top_p=0.95,
        top_k=40,
        max_output_tokens=512,
        response_mime_type="text/plain",
        tools=[google_search_tool],
    )
    
    try:
        response = client.models.generate_content(
            contents=all_groups_history,
            model="gemini-2.0-pro-exp-02-05",
            config=config,
        )
        return response.text.strip()
    except Exception as e:
        print(f"Error generating user taste: {e}")
        return "Error generating user taste profile."

def get_ai_recommendations(all_groups_history, user_taste):
    client = init_gemini_client()
    if not client:
        return "[]"
    
    # Format very explicitly to ensure valid JSON output
    system_instruction = (
        "You will receive a user's watch history and taste description.\n"
        "User Taste: " + user_taste + "\n\n"
        "The watch history format is:\n"
        "Watch History - Title: <title>, IMDB ID: <imdbID>, User Rating: <userRating>\n\n"
        f"Your task is to recommend exactly {NUM_MOVIES} movies and {NUM_SERIES} TV series.\n"
        "Do NOT recommend items that appear in the watch history.\n"
        "FORMAT: Return a JSON array with objects having exactly these fields:\n"
        "- \"title\": The title of the movie or show\n"
        "- \"imdb_id\": A valid IMDb ID starting with 'tt' followed by numbers\n"
        "- \"image_url\": Can be empty string or omitted\n"
        "EXAMPLE OUTPUT:\n"
        "[{\"title\":\"The Godfather\",\"imdb_id\":\"tt0068646\",\"image_url\":\"\"}]\n"
        "IMPORTANT: Return ONLY valid JSON with NO explanations or markdown formatting."
    )
    
    config = GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.7,
        top_p=0.95,
        top_k=40,
        max_output_tokens=8192,
        response_mime_type="application/json",  # Specify JSON output format
        tools=[google_search_tool],
    )
    
    try:
        response = client.models.generate_content(
            contents=all_groups_history,
            model="gemini-2.0-flash-exp",
            config=config,
        )
        return response.text
    except Exception as e:
        print(f"Error generating recommendations: {e}")
        return "[]" # Return empty JSON array on error

def get_tmdb_poster(imdb_id):
    if not TMDB_API_KEY or not imdb_id:
        return None
    url = f"{TMDB_BASE_URL}/find/{imdb_id}"
    params = {
        "api_key": TMDB_API_KEY,
        "external_source": "imdb_id"
    }
    try:
        r = requests.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        results = data.get("movie_results", [])
        if not results:
            results = data.get("tv_results", [])
        if results:
            poster_path = results[0].get("poster_path")
            if poster_path:
                return TMDB_IMAGE_BASE_URL + poster_path
    except Exception as e:
        print(f"Error fetching TMDB poster for {imdb_id}: {e}")
    return None

def update_recommendations_with_images(recommendations):
    updated = []
    for rec in recommendations:
        imdb_id = rec.get("imdb_id")
        tmdb_url = get_tmdb_poster(imdb_id)
        if tmdb_url:
            rec["image_url"] = tmdb_url
        updated.append(rec)
    return updated

def clean_json_output(text):
    """Super robust JSON cleaner with multiple fallback strategies"""
    if not text or text.isspace():
        print("Warning: Empty response from AI")
        return "[]"
    
    print(f"Raw AI output: {text[:100]}...")  # Print first 100 chars for debugging
    
    # Strategy 1: Basic Markdown cleaning
    cleaned = re.sub(r"```(json)?", "", text, flags=re.MULTILINE)
    cleaned = cleaned.strip()
    
    # Strategy 2: Find JSON array within text
    if not (cleaned.startswith('[') and cleaned.endswith(']')):
        match = re.search(r'\[\s*{.*}\s*\]', cleaned, re.DOTALL)
        if match:
            cleaned = match.group(0)
    
    # Strategy 3: Extract between first [ and last ]
    if not (cleaned.startswith('[') and cleaned.endswith(']')):
        start = cleaned.find('[')
        end = cleaned.rfind(']')
        if start >= 0 and end > start:
            cleaned = cleaned[start:end+1]
    
    # Strategy 4: Handle single quotes instead of double quotes
    if "'" in cleaned and '"' not in cleaned:
        cleaned = cleaned.replace("'", '"')
    
    # Strategy 5: Create a minimal valid array if all else fails
    if not cleaned or not cleaned.strip() or not (cleaned.startswith('[') and cleaned.endswith(']')):
        print("Warning: Could not extract valid JSON, returning empty array")
        return "[]"
    
    return cleaned

def filter_new_recommendations(recommendations, watch_history):
    watched_imdbs = {item[2] for item in watch_history}
    return [rec for rec in recommendations if rec.get("imdb_id") not in watched_imdbs]

def print_history_groups(db):
    raw_items = db.get_all_items()
    
    # Break early if no items
    if not raw_items:
        print("No watch history available. Skipping recommendations.")
        return
    
    unique_items = []
    seen = set()
    for item in raw_items:
        if item[2] and item[2] not in seen:
            seen.add(item[2])
            unique_items.append(item)
    
    # Break early if no unique items
    if not unique_items:
        print("No valid watch history with IMDB IDs. Skipping recommendations.")
        return
    
    all_groups_history = ""
    encoding = tiktoken.get_encoding("cl100k_base")
    
    for i in range(0, len(unique_items), ITEMS_PER_GROUP):
        group = unique_items[i:i + ITEMS_PER_GROUP]
        group_history = format_history_for_ai(group)
        token_count = len(encoding.encode(group_history))
        print(f"Group {(i // ITEMS_PER_GROUP) + 1} token count: {token_count}")
        all_groups_history += group_history + "\n" + ("-" * 50) + "\n"
    
    if not all_groups_history.strip():
        print("No watch history available. Skipping recommendations.")
        return
    
    # Get user taste
    print("Generating user taste...")
    new_taste = get_user_taste(all_groups_history)
    print("--- New User Taste ---")
    print(new_taste)
    
    # Compare with previous taste
    prev_taste = db.get_latest_user_taste(db.user_id)
    chosen_taste = prev_taste if prev_taste and len(new_taste.split()) < len(prev_taste.split()) else new_taste
    print("--- Chosen User Taste ---")
    print(chosen_taste)
    db.add_user_taste(db.user_id, chosen_taste)
    
    # Generate recommendations
    print("Generating recommendations...")
    final_recommendations_text = get_ai_recommendations(all_groups_history, chosen_taste)
    
    # Clean and parse JSON
    print("Processing recommendations...")
    cleaned_text = clean_json_output(final_recommendations_text)
    
    # Try multiple parsing strategies with detailed error logging
    recommendations = []
    try:
        print(f"Attempting to parse JSON: {cleaned_text[:100]}...")
        recommendations = json.loads(cleaned_text)
        print(f"Successfully parsed JSON with {len(recommendations)} items")
    except json.JSONDecodeError as je:
        print(f"Error parsing JSON: {je}")
        print(f"Problematic JSON: {cleaned_text}")
        
        # Create emergency fallback recommendations
        print("Creating fallback recommendations...")
        recommendations = [
            {"title": "The Shawshank Redemption", "imdb_id": "tt0111161", "image_url": ""},
            {"title": "The Godfather", "imdb_id": "tt0068646", "image_url": ""},
            {"title": "The Dark Knight", "imdb_id": "tt0468569", "image_url": ""},
            {"title": "Breaking Bad", "imdb_id": "tt0903747", "image_url": ""},
            {"title": "Game of Thrones", "imdb_id": "tt0944947", "image_url": ""}
        ]
    
    # Update with images 
    updated_recommendations = update_recommendations_with_images(recommendations)
    new_recommendations = filter_new_recommendations(updated_recommendations, unique_items)
    
    # Ensure we have enough recommendations
    if len(new_recommendations) < (NUM_MOVIES + NUM_SERIES):
        print("Not enough new recommendations generated, using fallback...")
        # Add some fallback recommendations if needed
        fallbacks = [
            {"title": "Inception", "imdb_id": "tt1375666", "image_url": ""},
            {"title": "The Matrix", "imdb_id": "tt0133093", "image_url": ""},
            {"title": "Stranger Things", "imdb_id": "tt4574334", "image_url": ""}
        ]
        
        # Add fallbacks that aren't in watch history
        for item in fallbacks:
            if item["imdb_id"] not in seen and len(new_recommendations) < (NUM_MOVIES + NUM_SERIES):
                new_recommendations.append(item)
                
        # Update these with images too
        new_recommendations = update_recommendations_with_images(new_recommendations)
    
    print(f"Final recommendations count: {len(new_recommendations)}")
    
    # Save recommendations to DB
    try:
        json_data = json.dumps(new_recommendations, ensure_ascii=False)
        db.add_recommendation("all", "AI Recommendations", "mixed", json_data)
        print("Successfully saved recommendations to database")
    except Exception as e:
        print(f"Error saving recommendations to database: {e}")
    
    # Push to Overseerr if configured
    if OVERSEERR_URL and OVERSEERR_API_TOKEN:
        push_result = push_discovery_recommendations(db.user_id, new_recommendations)
        if push_result:
            print("Successfully pushed recommendations to Overseerr.")
        else:
            print("Failed to push recommendations to Overseerr.")
    
    print("Recommendation process completed.")

def get_ai_search_results(query: str, system_instruction: str):
    client = init_gemini_client()
    if not client:
        return "[]"
        
    config = GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=1,
        top_p=0.95,
        top_k=40,
        max_output_tokens=1024,
        response_mime_type="text/plain",
        tools=[google_search_tool],
    )
    try:
        response = client.models.generate_content(
            contents=query,
            model="gemini-2.0-flash-exp",
            config=config,
        )
        return response.text
    except Exception as e:
        print(f"Error in AI search: {e}")
        return "[]"

def push_discovery_recommendations(user_id: str, recommendations: list):
    if not OVERSEERR_URL or not OVERSEERR_API_TOKEN:
        return False
        
    url = f"{OVERSEERR_URL}/api/v1/settings/discover/add"
    headers = {
        "Authorization": f"Bearer {OVERSEERR_API_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "userId": user_id,
        "recommendations": recommendations
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Error pushing discovery recommendations to Overseerr: {e}")
        return None

def generate_discovery_recommendations(user_id: str, gemini_api_key: str, tmdb_api_key: str, num_movies: int, num_series: int, extra_elements: str):
    # Set environment variables for this function call
    os.environ["GEMINI_API_KEY"] = gemini_api_key
    os.environ["TMDB_API_KEY"] = tmdb_api_key
    
    db = Database(user_id)
    
    items = db.get_all_items()
    user_history_text = ""
    for row in items:
        if row[1] and row[2]:  # Make sure we have title and imdb_id
            user_history_text += f"Watch History - Title: {row[1]}, IMDB ID: {row[2]}, User Rating: {row[3]}\n"
    
    taste = db.get_latest_user_taste(user_id) or ""
    
    system_instruction = (
        "You will receive a user's watch history, taste description, and discovery elements.\n\n"
        f"User Taste: {taste}\n"
        f"Discovery Elements: {extra_elements}\n\n"
        f"{user_history_text}\n\n"
        f"Your task is to recommend {num_movies} movies and {num_series} TV series.\n"
        "Do NOT recommend items that appear in the watch history.\n"
        "FORMAT: Return a JSON array with exactly these fields:\n"
        "- \"title\": The title of the movie or show\n"
        "- \"imdb_id\": A valid IMDb ID starting with 'tt' followed by numbers\n"
        "- \"image_url\": Can be empty string\n"
        "IMPORTANT: Return ONLY valid JSON with NO explanations."
    )
    
    client = init_gemini_client()
    if not client:
        return []
        
    config = GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.7,
        top_p=0.95,
        top_k=40,
        max_output_tokens=8192,
        response_mime_type="application/json",
        tools=[google_search_tool],
    )
    
    try:
        response = client.models.generate_content(
            contents="",
            model="gemini-2.0-flash-exp",
            config=config,
        )
        raw_output = response.text
        cleaned_text = clean_json_output(raw_output)
        recommendations = json.loads(cleaned_text)
    except json.JSONDecodeError as je:
        print(f"Error parsing JSON: {je}")
        recommendations = []
    except Exception as e:
        print(f"Error generating discovery recommendations: {e}")
        recommendations = []
    
    updated_recommendations = update_recommendations_with_images(recommendations)
    watched_imdbs = {row[2] for row in items if row[2]}
    final_recs = [r for r in updated_recommendations if r.get("imdb_id") not in watched_imdbs]
    
    # Save recommendations to database
    try:
        db.add_recommendation("discovery", "Discovery Recommendations", "mixed", 
                            json.dumps(final_recs, ensure_ascii=False))
    except Exception as e:
        print(f"Error saving discovery recommendations: {e}")
    
    # Push to Overseerr if configured
    if OVERSEERR_URL and OVERSEERR_API_TOKEN:
        push_result = push_discovery_recommendations(user_id, final_recs)
        if push_result:
            print("Discovery recommendations pushed to Overseerr successfully.")
        else:
            print("Failed to push discovery recommendations to Overseerr.")
            
    return final_recs
