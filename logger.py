### logger.py

import threading
import os
from datetime import datetime

LOG_PATH = "/home/lds00/sprinkler/sprinkler_status.log"
_log_lock = threading.Lock()

def log(msg):
    ts = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{ts} {msg}\n"
    with _log_lock:
        with open(LOG_PATH, "a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())