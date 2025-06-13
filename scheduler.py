from datetime import datetime
import json
from datetime import datetime
import time
import os

def load_json(path):
    with open(path, 'r') as f:
        return json.load(f)

def get_schedule_day_index():
    base = datetime(2023, 12, 31)  # Sunday
    today = datetime.now().date()
    idx = (today - base.date()).days % 14
    return idx

def should_run_today(schedule):
    idx = get_schedule_day_index()
    return schedule.get("schedule_days", [False] * 14)[idx]

def is_start_time_enabled(schedule, time_str):
    return any(entry.get("time") == time_str and entry.get("isEnabled", False)
               for entry in schedule.get("start_times", []))

def get_mist_flags(schedule, time_str):
    mist = schedule.get("mist", {})
    return mist.get(f"time_{time_str.replace(':', '')}", False)

def is_active(set_entry):
    # "Misters" are always active (run via mist schedule)
    if set_entry.get("set_name") == "Misters":
        return True
    return set_entry.get("mode", True)
