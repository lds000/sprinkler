### logger.py

from datetime import datetime

LOG_FILE = "/home/lds00/sprinkler/sprinkler_status.log"
declare_log = []

MAX_LOG_LINES = 1000

def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{now}] {message}"
    print(entry, flush=True)
    declare_log.append(entry)
    # Keep only the last MAX_LOG_LINES in memory
    if len(declare_log) > MAX_LOG_LINES:
        declare_log[:] = declare_log[-MAX_LOG_LINES:]
    # Write only the last MAX_LOG_LINES to file
    try:
        with open(LOG_FILE, "w") as f:
            for line in declare_log:
                f.write(line + "\n")
    except Exception as e:
        print(f"[LOGGER ERROR] Failed to write log file: {e}", flush=True)