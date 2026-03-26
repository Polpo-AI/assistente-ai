"""Supabase client singleton — condiviso da tutti i moduli db/."""

import os
import threading
from typing import Optional
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_supabase: Optional[Client] = None
_supabase_lock = threading.Lock()


def get_client() -> Client:
    global _supabase
    if _supabase is None:
        with _supabase_lock:
            if _supabase is None:
                url = os.environ["SUPABASE_URL"]
                key = os.environ["SUPABASE_KEY"]
                _supabase = create_client(url, key)
    return _supabase
