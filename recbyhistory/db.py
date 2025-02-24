import sqlite3
import os
from datetime import datetime

class Database:
    def __init__(self, user_id):
        self.user_id = user_id
        self.db_path = os.path.join(user_id, "db")
        os.makedirs(self.db_path, exist_ok=True)
        self.db_file = os.path.join(self.db_path, "watch_history.db")
        
        self.conn = sqlite3.connect(self.db_file)
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        
        # We no longer drop watch_history on every run,
        # we just create if not exists (with a UNIQUE constraint to avoid duplicates).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS watch_history (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                imdb_id TEXT NOT NULL,
                user_rating FLOAT,
                resolution TEXT,
                added_at TIMESTAMP,
                UNIQUE (user_id, imdb_id, resolution) ON CONFLICT IGNORE
            )
        ''')

        # all_items remains persistent
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS all_items (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                imdb_id TEXT,
                user_rating FLOAT,
                resolution TEXT,
                added_at TIMESTAMP,
                UNIQUE (imdb_id, resolution) ON CONFLICT IGNORE
            )
        ''')

        # Recommendations table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_recommendations (
                id INTEGER PRIMARY KEY,
                group_id TEXT,
                title TEXT NOT NULL,
                imdb_id TEXT NOT NULL,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # User taste table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_taste (
                id INTEGER PRIMARY KEY,
                user_name TEXT NOT NULL,
                taste TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()

    # Functions for watch_history and all_items
    def add_item(self, title, imdb_id, user_rating, resolution):
        """
        Insert a record for watch_history with user_id,
        avoiding duplicates via the UNIQUE constraint on (user_id, imdb_id, resolution).
        """
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO watch_history (user_id, title, imdb_id, user_rating, resolution, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (self.user_id, title, imdb_id, user_rating, resolution, datetime.now()))
        self.conn.commit()

    def add_all_item(self, title, imdb_id, user_rating, resolution):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO all_items (title, imdb_id, user_rating, resolution, added_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (title, imdb_id, user_rating, resolution, datetime.now()))
        self.conn.commit()

    def get_all_items(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM watch_history ORDER BY added_at DESC')
        return cursor.fetchall()

    def get_all_library_items(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM all_items ORDER BY added_at DESC')
        return cursor.fetchall()

    def get_items_by_title(self, title):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM watch_history WHERE title LIKE ?', (f'%{title}%',))
        return cursor.fetchall()

    def get_items_by_imdb(self, imdb_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM watch_history WHERE imdb_id = ?', (imdb_id,))
        return cursor.fetchall()

    # Functions for ai_recommendations
    def add_recommendation(self, group_id, title, media_type, recommendation_text):
        """
        Insert multiple AI-driven recommendations (parsed from JSON) into ai_recommendations table.
        """
        cursor = self.conn.cursor()
        try:
            import json
            recommendations = json.loads(recommendation_text)
        except Exception as e:
            print(f"Error parsing recommendation_text JSON: {e}")
            recommendations = []
        for rec in recommendations:
            rec_title = rec.get("title", "")
            rec_imdb = rec.get("imdb_id", "")
            rec_image = rec.get("image_url", "")
            cursor.execute('''
                INSERT INTO ai_recommendations (group_id, title, imdb_id, image_url)
                VALUES (?, ?, ?, ?)
            ''', (group_id, rec_title, rec_imdb, rec_image))
        self.conn.commit()

    # Functions for user_taste
    def add_user_taste(self, user_name, taste):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO user_taste (user_name, taste)
            VALUES (?, ?)
        ''', (user_name, taste))
        self.conn.commit()

    def get_latest_user_taste(self, user_name):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT taste FROM user_taste 
            WHERE user_name = ?
            ORDER BY updated_at DESC 
            LIMIT 1
        ''', (user_name,))
        row = cursor.fetchone()
        return row[0] if row else None
