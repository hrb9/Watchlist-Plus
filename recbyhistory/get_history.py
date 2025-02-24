# recbyhistory/get_history.py
import logging
from imdb_id_service import IMDBServiceClient
from auth_client import PlexAuthClient



class PlexHistory:
    def __init__(self, user_id):
        self.imdb_service = IMDBServiceClient()
        # יצירת מופע של PlexAuthClient
        auth_client = PlexAuthClient()  
        self.servers = auth_client.connect_to_plex(user_id)
        if not self.servers:
            logging.error(f"Failed to connect to Plex for user {user_id}")
    
    def get_item_resolution(self, item):
        try:
            if hasattr(item, 'media') and item.media:
                media = item.media[0]
                if hasattr(media, 'videoResolution'):
                    return media.videoResolution
            return "Unknown"
        except Exception as e:
            logging.error(f"Error getting resolution for {item.title}: {e}")
            return "Unknown"

    def get_imdb_id(self, item):
        title = item.title if item.title else ""
        if not title:
            return None
        return self.imdb_service.get_imdb_id(item)

    def get_user_rating(self, item):
        try:
            if item.type in ['show', 'episode']:
                try:
                    show = getattr(item, 'show', lambda: None)()
                except Exception as e:
                    logging.error(f"Error fetching show for {item.title}: {e}")
                    show = None
                if show and hasattr(show, 'userRating') and show.userRating != 0.0:
                    return show.userRating
                if hasattr(item, 'userRating') and item.userRating != 0.0:
                    return item.userRating
            elif item.type == 'movie':
                if hasattr(item, 'userRating') and item.userRating != 0.0:
                    return item.userRating
            return 0.0
        except Exception as e:
            logging.error(f"Error getting user rating for {item.title}: {e}")
            return 0.0

    def get_watch_history(self, db):
        """
        1. עובר על כל השרתים/ספריות, מאתר פריטים.
        2. עבור פריטים ש-isWatched=True, מוסיף למערך (history) וגם כותב לטבלת watch_history ב-DB.
        3. מחזיר את המערך history, כך שאפשר להמשיך לעבדו בקוד חיצוני אם צריך.
        """
        history = []
        grouped_episodes = {}
        seen_movies = set()

        for server in self.servers:
            for lib in server.library.sections():
                for item in lib.all():
                    if item.type in ['show', 'episode', 'movie']:
                        ratingKey = item.ratingKey
                        if ratingKey is not None:
                            # טוען מחדש את הפריט
                            item = item._server.fetchItem(ratingKey)

                        resolution = self.get_item_resolution(item)
                        user_rating = self.get_user_rating(item)
                        imdb_id = self.get_imdb_id(item)
                        title = item.title if item.title else "Untitled"

                        # מוסיף ל-all_items (למשל db.add_all_item(...)) אם צריך
                        db.add_all_item(title, imdb_id, user_rating, resolution)

                        # רק אם נצפה
                        if item.isWatched:
                            if not imdb_id:
                                # אם אין imdb_id, ממשיכים
                                continue

                            if item.type == 'episode':
                                if item.type == 'episode':
                                    try:
                                        show = getattr(item, 'show', lambda: None)()
                                    except Exception as e:
                                        logging.error(f"Error fetching show for {item.title}: {e}")
                                        show = None

                                    if show:
                                        show_title = show.title
                                        show_imdb = self.get_imdb_id(show)
                                        show_rating = show.userRating if (hasattr(show, 'userRating') and show.userRating != 0.0) else user_rating
                                        show_resolution = self.get_item_resolution(show)

                                        # (1) Add the show-level record to watch_history as well
                                        if show_imdb:
                                            db.add_item(
                                                title=show_title,
                                                imdb_id=show_imdb,
                                                user_rating=show_rating,
                                                resolution=show_resolution
                                            )

                                        # (2) Continue adding the episode
                                        episode_rating = self.get_user_rating(item)
                                        if episode_rating == 0.0:
                                            episode_rating = show_rating
                                        episode_imdb = self.get_imdb_id(item)
                                        episode_resolution = resolution

                                        grouped_episodes[show_title]['episodes'].append({
                                            'title': item.title,
                                            'imdbID': episode_imdb,
                                            'userRating': episode_rating,
                                            'resolution': episode_resolution
                                        })

                                        db.add_item(
                                            title=item.title,
                                            imdb_id=episode_imdb,
                                            user_rating=episode_rating,
                                            resolution=episode_resolution
                                        )
                                        print(f"Added episode {item.title} to {show_title}")
                                    else:
                                        # אם לא הצלחנו למצוא show, נוסיף את הפרק כפריט רגיל
                                        info = {
                                            'title': title,
                                            'imdbID': imdb_id,
                                            'userRating': user_rating,
                                            'resolution': resolution
                                        }
                                        history.append(info)
                                        db.add_item(
                                            title=title,
                                            imdb_id=imdb_id,
                                            user_rating=user_rating,
                                            resolution=resolution
                                        )
                            elif item.type == 'movie':
                                if imdb_id in seen_movies:
                                    # כבר ראינו סרט זה
                                    continue
                                seen_movies.add(imdb_id)

                                info = {
                                    'title': title,
                                    'imdbID': imdb_id,
                                    'userRating': user_rating,
                                    'resolution': resolution
                                }
                                history.append(info)

                                # מוסיף לטבלת watch_history
                                db.add_item(
                                    title=title,
                                    imdb_id=imdb_id,
                                    user_rating=user_rating,
                                    resolution=resolution
                                )
                                print(f"Added movie {title}")
                            else:
                                # item.type == 'show'
                                info = {
                                    'title': title,
                                    'imdbID': imdb_id,
                                    'userRating': user_rating,
                                    'resolution': resolution
                                }
                                history.append(info)
                                db.add_item(
                                    title=title,
                                    imdb_id=imdb_id,
                                    user_rating=user_rating,
                                    resolution=resolution
                                )
                                print(f"Added show {title}")

        # איחוד הסדרות (grouped episodes) אל ה-history
        for show_title, show_data in grouped_episodes.items():
            history.append(show_data)

        return history