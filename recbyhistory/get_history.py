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
        history = []
        grouped_episodes = {}
        seen_movies = set()

        for server in self.servers:
            for lib in server.library.sections():
                for item in lib.all():
                    if item.type in ['show', 'episode', 'movie']:
                        ratingKey = item.ratingKey
                        if ratingKey is not None:
                            item = item._server.fetchItem(ratingKey)
                        
                        resolution = self.get_item_resolution(item)
                        user_rating = self.get_user_rating(item)
                        imdb_id = self.get_imdb_id(item)
                        title = item.title if item.title else "Untitled"

                        db.add_all_item(title, imdb_id, user_rating, resolution)

                        if item.isWatched:
                            print(item.title)
                            if not imdb_id:
                                continue
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
                                    if show_title not in grouped_episodes:
                                        grouped_episodes[show_title] = {
                                            'title': show_title,
                                            'imdbID': show_imdb,
                                            'userRating': show_rating,
                                            'resolution': show_resolution,
                                            'episodes': []
                                        }
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
                                else:
                                    history.append({
                                        'title': title,
                                        'imdbID': imdb_id,
                                        'userRating': user_rating,
                                        'resolution': resolution
                                    })
                            elif item.type == 'movie':
                                if imdb_id in seen_movies:
                                    continue
                                seen_movies.add(imdb_id)
                                history.append({
                                    'title': title,
                                    'imdbID': imdb_id,
                                    'userRating': user_rating,
                                    'resolution': resolution
                                })
                            else:
                                history.append({
                                    'title': title,
                                    'imdbID': imdb_id,
                                    'userRating': user_rating,
                                    'resolution': resolution
                                })
        for show_data in grouped_episodes.values():
            history.append(show_data)
        return history
