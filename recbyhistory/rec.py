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
from config import ITEMS_PER_GROUP

# Control variables for recommendations
NUM_MOVIES = 3
NUM_SERIES = 2
RATING_THRESHOLD = 5.0

# TMDB configuration
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

# Gemini client setup
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
        "Include specifics such as preferred genres, styles, directors, and any unique characteristics. The description should be insightful "
        "and clearly reveal what kind of movies and TV series would best match the user's preferences. Return only the detailed description."
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
        "You will receive a user's *complete* watch history and a description of the user's taste.\n"
        "User Taste: " + user_taste + "\n\n"
        "The watch history will be provided in the following format:\n\n"
        "Watch History - Title: <title>, IMDB ID: <imdbID>, User Rating: <userRating>\n\n"
        f"Your task is to analyze the watch history along with the user's taste, and recommend {NUM_MOVIES} movies and {NUM_SERIES} TV series that the user will enjoy. "
        "IMPORTANT: Do NOT recommend items that appear in the watch history or share the same IMDb ID as any watch history item.\n"
        "Return the recommendations as a JSON array. Each element must be a JSON object with the keys: 'title', 'imdb_id', and 'image_url'."
    )
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
    cleaned = re.sub(r"^```(json)?", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"```$", "", cleaned, flags=re.MULTILINE)
    return cleaned.strip()

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
        group_id = (i // ITEMS_PER_GROUP) + 1
        
        print(f"\nGroup {group_id}:")
        for item in group:
            rating = item[3] if item[3] is not None else "N/A"
            print(f"Watch History - Title: {item[1]}, IMDB ID: {item[2]}, User Rating: {rating}")
        
        group_history = format_history_for_ai(group)
        token_count = len(encoding.encode(group_history))
        print(f"Group {group_id} token count: {token_count}")
        all_groups_history += group_history
        print("-" * 50)
    
    if not all_groups_history.strip():
        print("No watch history available. Skipping recommendations.")
        return
    
    new_taste = get_user_taste(all_groups_history)
    print("\n--- New User Taste ---")
    print(new_taste)
    
    prev_taste = db.get_latest_user_taste(db.user_id)
    if prev_taste:
        print("\n--- Previous User Taste ---")
        print(prev_taste)
    chosen_taste = prev_taste if prev_taste and len(new_taste.split()) < len(prev_taste.split()) else new_taste
    print("\n--- Chosen User Taste ---")
    print(chosen_taste)
    db.add_user_taste(db.user_id, chosen_taste)
    
    final_recommendations_text = get_ai_recommendations(all_groups_history, chosen_taste)
    print("\n--- Final AI Recommendations (raw output) ---")
    print(final_recommendations_text)
    
    cleaned_text = clean_json_output(final_recommendations_text)
    try:
        recommendations = json.loads(cleaned_text)
    except json.JSONDecodeError as je:
        print("Error parsing JSON from AI recommendations:", je)
        recommendations = []
    
    updated_recommendations = update_recommendations_with_images(recommendations)
    print("\n--- Updated Recommendations with TMDB Images ---")
    print(json.dumps(updated_recommendations, ensure_ascii=False, indent=2))
    
    new_recommendations = filter_new_recommendations(updated_recommendations, unique_items)
    
    if len(new_recommendations) < (NUM_MOVIES + NUM_SERIES):
        print("\nNot enough new recommendations generated, regenerating...")
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
        print("\n--- Regenerated Updated Recommendations with TMDB Images ---")
        print(json.dumps(new_recommendations, ensure_ascii=False, indent=2))
    else:
        print("\n--- Final New Recommendations (not in watch history) ---")
        print(json.dumps(new_recommendations, ensure_ascii=False, indent=2))
    
    db.add_recommendation("all", "AI Recommendations", "mixed", json.dumps(new_recommendations, ensure_ascii=False))
    
    time.sleep(10)  # Rate limiting

def generate_discovery_recommendations(
    user_id: str,
    gemini_api_key: str,
    tmdb_api_key: str,
    num_movies: int,
    num_series: int,
    extra_elements: str
):
    db = Database(user_id)
    
    items = db.get_all_items()  
    user_history_text = ""
    for row in items:
        user_history_text += f"Watch History - Title: {row[1]}, IMDB ID: {row[2]}, User Rating: {row[3]}\n"

    taste = db.get_latest_user_taste(user_id) or ""

    system_instruction = (
        "You will receive a user's *complete* watch history, a description of the user's taste, "
        "and additional 'discovery' elements to consider.\n\n"
        f"User Taste: {taste}\n"
        f"Discovery Elements: {extra_elements}\n\n"
        f"{user_history_text}\n\n"
        f"Your task is to recommend {num_movies} movies and {num_series} TV series. "
        "IMPORTANT: Do NOT recommend items that appear in the watch history. "
        "Return the recommendations as a JSON array with objects containing 'title', 'imdb_id', 'image_url'."
    )

    os.environ["GEMINI_API_KEY"] = gemini_api_key
    os.environ["TMDB_API_KEY"] = tmdb_api_key

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
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
    except json.JSONDecodeError:
        recommendations = []
    
    updated = update_recommendations_with_images(recommendations)
    
    watch_imdbs = {row[2] for row in items}  
    final_recs = [r for r in updated if r.get("imdb_id") not in watch_imdbs]

    rec_text = json.dumps(final_recs, ensure_ascii=False)
    db.add_recommendation("discovery", "Discovery Recommendations", "mixed", rec_text)

    return final_recs    

if __name__ == "__main__":
    user_id = "haj9"
    db = Database(user_id)
    print_history_groups(db)
