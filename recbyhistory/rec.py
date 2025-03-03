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

# Initialize Gemini client and Google search tool
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
google_search_tool = Tool(
    google_search=GoogleSearch()
)

def format_history_for_ai(group):
    prompt = ""
    for item in group:
        rating = item[3] if item[3] is not None else "N/A"
        prompt += f"Watch History - Title: {item[1]}, IMDB ID: {item[2]}, User Rating: {rating}\n"
    return prompt

def get_user_taste(all_groups_history):
    if not all_groups_history.strip():
        return "No watch history available to determine user taste."
    
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
    response = client.models.generate_content(
        contents=all_groups_history,
        model="gemini-2.0-pro-exp-02-05",
        config=config,
    )
    return response.text.strip()

def get_ai_recommendations(all_groups_history, user_taste):
    system_instruction = (
        "You will receive a user's complete watch history and a description of the user's taste.\n"
        "User Taste: " + user_taste + "\n\n"
        "The watch history is provided as:\n\n"
        "Watch History - Title: <title>, IMDB ID: <imdbID>, User Rating: <userRating>\n\n"
        f"Your task is to recommend {NUM_MOVIES} movies and {NUM_SERIES} TV series that the user will enjoy. "
        "Do NOT recommend items that appear in the watch history. "
        "IMPORTANT: You MUST return ONLY a valid JSON array with objects containing 'title', 'imdb_id', and 'image_url'. "
        "Do not include any explanation, commentary, or markdown formatting like ```json."
    )
    config = GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.7,  # Reduced from 1.0 to make output more consistent
        top_p=0.95,
        top_k=40,
        max_output_tokens=8192,
        response_mime_type="application/json",  # Changed to application/json
        tools=[google_search_tool],
    )
    response = client.models.generate_content(
        contents=all_groups_history,
        model="gemini-2.0-flash-exp",
        config=config,
    )
    return response.text

def get_tmdb_poster(imdb_id):
    if not TMDB_API_KEY:
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
    """Improved function to extract JSON from AI responses"""
    if not text or text.isspace():
        print("Warning: Empty response from AI")
        return "[]"
        
    # First try the standard cleaning
    cleaned = re.sub(r"^```(json)?", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"```$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()
    
    # Check if we have what looks like JSON
    if cleaned and (cleaned.startswith('[') or cleaned.startswith('{')):
        return cleaned
        
    # Try to find JSON array within the text using regex
    json_match = re.search(r'\[\s*{.*}\s*\]', text, re.DOTALL)
    if json_match:
        return json_match.group(0)
    
    # Try to extract just the part between opening and closing brackets
    try:
        start_idx = text.find('[')
        end_idx = text.rfind(']')
        if start_idx >= 0 and end_idx > start_idx:
            return text[start_idx:end_idx+1]
    except:
        pass
    
    print("Warning: Could not find valid JSON in response")
    return "[]"  # Return empty array as fallback

def filter_new_recommendations(recommendations, watch_history):
    watched_imdbs = {item[2] for item in watch_history}
    return [rec for rec in recommendations if rec.get("imdb_id") not in watched_imdbs]

def print_history_groups(db):
    raw_items = db.get_all_items()
    unique_items = []
    seen = set()
    for item in raw_items:
        if item[2] in seen:
            continue
        seen.add(item[2])
        unique_items.append(item)
    
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
    
    new_taste = get_user_taste(all_groups_history)
    print("--- New User Taste ---")
    print(new_taste)
    
    prev_taste = db.get_latest_user_taste(db.user_id)
    chosen_taste = prev_taste if prev_taste and len(new_taste.split()) < len(prev_taste.split()) else new_taste
    print("--- Chosen User Taste ---")
    print(chosen_taste)
    db.add_user_taste(db.user_id, chosen_taste)
    
    final_recommendations_text = get_ai_recommendations(all_groups_history, chosen_taste)
    print("--- Final AI Recommendations (raw output) ---")
    print(final_recommendations_text)
    
    cleaned_text = clean_json_output(final_recommendations_text)
    
    # Try multiple parsing strategies
    recommendations = []
    try:
        recommendations = json.loads(cleaned_text)
    except json.JSONDecodeError as je:
        print(f"Error parsing JSON from AI recommendations: {je}")
        
        # Try to fix common JSON issues
        try:
            # Replace single quotes with double quotes
            fixed_text = cleaned_text.replace("'", '"')
            recommendations = json.loads(fixed_text)
            print("Successfully parsed JSON after fixing quotes")
        except json.JSONDecodeError:
            # Try just extracting items with imdb_id using regex
            print("Attempting to extract recommendations with regex...")
            pattern = r'{[^{}]*"imdb_id"[^{}]*}'
            matches = re.findall(pattern, cleaned_text)
            if matches:
                try:
                    collected_items = "[" + ",".join(matches) + "]"
                    recommendations = json.loads(collected_items)
                    print(f"Extracted {len(recommendations)} recommendations with regex")
                except:
                    print("Failed to parse extracted items")
                    recommendations = []
            else:
                print("No items found with regex, using empty list")
                recommendations = []
    
    updated_recommendations = update_recommendations_with_images(recommendations)
    new_recommendations = filter_new_recommendations(updated_recommendations, unique_items)
    
    if len(new_recommendations) < (NUM_MOVIES + NUM_SERIES):
        print("Not enough new recommendations generated, regenerating...")
        regenerated_taste = get_user_taste(all_groups_history)
        print("Regenerated User Taste:", regenerated_taste)
        db.add_user_taste(db.user_id, regenerated_taste)
        final_recommendations_text = get_ai_recommendations(all_groups_history, regenerated_taste)
        cleaned_text = clean_json_output(final_recommendations_text)
        try:
            recommendations = json.loads(cleaned_text)
        except json.JSONDecodeError as je:
            print("Error parsing JSON from regenerated AI recommendations:", je)
            recommendations = []
        updated_recommendations = update_recommendations_with_images(recommendations)
        new_recommendations = filter_new_recommendations(updated_recommendations, unique_items)
    else:
        print("Final new recommendations (not in watch history):")
        print(json.dumps(new_recommendations, ensure_ascii=False, indent=2))
    
    # Save recommendations under group "all" in the DB
    db.add_recommendation("all", "AI Recommendations", "mixed", json.dumps(new_recommendations, ensure_ascii=False))
    
    # Push discovery recommendations to Overseerr via its API
    push_result = push_discovery_recommendations(db.user_id, new_recommendations)
    if push_result:
        print("Successfully pushed discovery recommendations to Overseerr.")
    else:
        print("Failed to push discovery recommendations to Overseerr.")
    
    time.sleep(10)  # Rate limiting

def get_ai_search_results(query: str, system_instruction: str):
    """
    Performs an AI-based search using the given query and system instruction.
    Returns the raw text response.
    """
    config = GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=1,
        top_p=0.95,
        top_k=40,
        max_output_tokens=1024,
        response_mime_type="text/plain",
        tools=[google_search_tool],
    )
    response = client.models.generate_content(
        contents=query,
        model="gemini-2.0-flash-exp",
        config=config,
    )
    return response.text

def push_discovery_recommendations(user_id: str, recommendations: list):
    """
    Pushes the discovery recommendations to Overseerr using the /settings/discover/add endpoint.
    Expects recommendations as a list of dictionaries with keys 'title', 'imdb_id', and 'image_url'.
    """
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
        print(f"Error pushing discovery recommendations to Overseerr for user {user_id}: {e}")
        return None

def generate_discovery_recommendations(user_id: str, gemini_api_key: str, tmdb_api_key: str, num_movies: int, num_series: int, extra_elements: str):
    """
    Generates "Discovery" recommendations for the given user.
    Retrieves the user's watch history and taste, builds a prompt with extra discovery elements,
    uses the Gemini AI model to generate recommendations, filters out items already watched,
    saves them in the DB under group "discovery", and pushes them to Overseerr.
    """
    db = Database(user_id)
    
    items = db.get_all_items()
    user_history_text = ""
    for row in items:
        user_history_text += f"Watch History - Title: {row[1]}, IMDB ID: {row[2]}, User Rating: {row[3]}\n"
    
    taste = db.get_latest_user_taste(user_id) or ""
    
    system_instruction = (
        "You will receive a user's complete watch history, a description of the user's taste, "
        "and additional 'discovery' elements to consider.\n\n"
        f"User Taste: {taste}\n"
        f"Discovery Elements: {extra_elements}\n\n"
        f"{user_history_text}\n\n"
        f"Your task is to recommend {num_movies} movies and {num_series} TV series that the user will enjoy. "
        "Do NOT recommend items that appear in the watch history. "
        "Return the recommendations as a JSON array with objects containing 'title', 'imdb_id', and 'image_url'."
    )
    
    os.environ["GEMINI_API_KEY"] = gemini_api_key
    os.environ["TMDB_API_KEY"] = tmdb_api_key
    
    config = GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=1,
        top_p=0.95,
        top_k=40,
        max_output_tokens=8192,
        response_mime_type="text/plain",
        tools=[google_search_tool],
    )
    response = client.models.generate_content(
        contents="",
        model="gemini-2.0-flash-exp",
        config=config,
    )
    raw_output = response.text
    cleaned_text = clean_json_output(raw_output)
    try:
        recommendations = json.loads(cleaned_text)
    except json.JSONDecodeError as je:
        print("Error parsing JSON from AI recommendations:", je)
        recommendations = []
    
    updated_recommendations = update_recommendations_with_images(recommendations)
    watched_imdbs = {row[2] for row in items}
    final_recs = [r for r in updated_recommendations if r.get("imdb_id") not in watched_imdbs]
    
    db.add_recommendation("discovery", "Discovery Recommendations", "mixed", json.dumps(final_recs, ensure_ascii=False))
    # Push recommendations to Overseerr
    push_result = push_discovery_recommendations(user_id, final_recs)
    if push_result:
        print("Discovery recommendations pushed to Overseerr successfully.")
    else:
        print("Failed to push discovery recommendations to Overseerr.")
    return final_recs
