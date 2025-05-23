### run_manager.py

import time
from datetime import datetime
from gpio_controller import turn_on, turn_off
from status import CURRENT_RUN
from logger import log
from config import RELAYS  # ✅ Correct source for RELAYS
import json
from datetime import datetime, timedelta
WATERING_HISTORY_JSONL = "/home/lds00/sprinkler/watering_history.jsonl"

LAST_COMPLETED_RUN_FILE = "/home/lds00/sprinkler/last_completed_run.json"


def force_stop_all():
    for name, pin in RELAYS.items():
        turn_off(pin, name)
    CURRENT_RUN.update({
        "Running": False,
        "Set": "",
        "Time_Remaining_Sec": 0,
        "Soak_Remaining_Sec": 0,
        "Phase": ""
    })
    log("[SYSTEM] All zones stopped manually.")


def log_watering_history(log_file, set_name, start_dt, end_dt, source="SCHEDULED", status="Completed", duration_minutes=None):
    entry = f"{start_dt.date()} {set_name} {source.upper()} START: {start_dt.strftime('%H:%M:%S')} STOP: {end_dt.strftime('%H:%M:%S')}\n"
    with open(log_file, "a") as f:
        f.write(entry)
    # Record last completed run for status API
    try:
        last_run = {
            "set": set_name,
            "end_time": end_dt.isoformat(),
            "duration_minutes": duration_minutes if duration_minutes is not None else int((end_dt - start_dt).total_seconds() // 60),
            "status": status
        }
        with open(LAST_COMPLETED_RUN_FILE, "w") as f:
            json.dump(last_run, f)
    except Exception as e:
        log(f"[WARN] Could not write last_completed_run.json: {e}")
    # --- Persistent JSONL watering history ---
    try:
        event = {
            "date": start_dt.isoformat(),
            "set": set_name,
            "duration_minutes": duration_minutes if duration_minutes is not None else int((end_dt - start_dt).total_seconds() // 60),
            "status": status
        }
        with open(WATERING_HISTORY_JSONL, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as e:
        log(f"[WARN] Could not write watering_history.jsonl: {e}")


def run_set(set_name, duration_minutes, RELAYS, log_file, source="SCHEDULED", pulse=None, soak=None):
    pin = RELAYS.get(set_name)
    if pin is None:
        log(f"[ERROR] Unknown set name: {set_name}")
        return

    log(f"[SET] Running {set_name} for {duration_minutes} min ({source})")
    start_time = datetime.now()
    total = duration_minutes * 60
    elapsed = 0
    CURRENT_RUN.update({
        "Running": True,
        "Set": set_name,
        "Phase": "Watering",
        "Time_Remaining_Sec": total,
        "Soak_Remaining_Sec": 0,
        "Pulse_Time_Left_Sec": 0
    })

    log(f"[DEBUG] Turning ON relay for {set_name} (pin {pin}) at {datetime.now().isoformat()}")
    if pulse and soak:
        while elapsed < total:
            CURRENT_RUN["Phase"] = "Watering"
            turn_on(pin)
            pulse_left = pulse * 60
            for i in range(pulse * 60):
                if elapsed >= total:
                    break
                time.sleep(1)
                elapsed += 1
                CURRENT_RUN["Time_Remaining_Sec"] = total - elapsed
                CURRENT_RUN["Pulse_Time_Left_Sec"] = pulse_left
                pulse_left -= 1
            turn_off(pin)
            log(f"[DEBUG] Turned OFF relay for {set_name} (pin {pin}) after pulse at {datetime.now().isoformat()}")
            CURRENT_RUN["Pulse_Time_Left_Sec"] = 0
            if elapsed < total:
                CURRENT_RUN["Phase"] = "Soaking"
                CURRENT_RUN["Soak_Remaining_Sec"] = soak * 60
                for i in range(soak * 60):
                    time.sleep(1)
                    CURRENT_RUN["Soak_Remaining_Sec"] = soak * 60 - (i + 1)
    else:
        turn_on(pin)
        log(f"[DEBUG] Relay for {set_name} (pin {pin}) should now be ON for {total} seconds")
        for i in range(total):
            time.sleep(1)
            CURRENT_RUN.update({
                "Running": True,
                "Set": set_name,
                "Time_Remaining_Sec": total - i,
                "Phase": "Watering",
                "Soak_Remaining_Sec": 0,
                "Pulse_Time_Left_Sec": 0
            })
        turn_off(pin)
        log(f"[DEBUG] Turned OFF relay for {set_name} (pin {pin}) after watering at {datetime.now().isoformat()}")

    turn_off(pin)
    end_time = datetime.now()
    CURRENT_RUN.update({
        "Running": False,
        "Set": "",
        "Time_Remaining_Sec": 0,
        "Soak_Remaining_Sec": 0,
        "Phase": "",
        "Pulse_Time_Left_Sec": 0
    })
    log_watering_history(log_file, set_name, start_time, end_time, source, status="Completed", duration_minutes=duration_minutes)
    log(f"[SET] Completed {set_name}")
