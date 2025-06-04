from flask import Flask, jsonify, request
from status import CURRENT_RUN
from run_manager import force_stop_all
import os
from scheduler import get_schedule_day_index, load_json
from datetime import datetime, timedelta
import time
from logger import declare_log, log
from gpio_controller import get_led_colors
import json

TEST_MODE_FILE = "/home/lds00/sprinkler/test_mode.txt"
LAST_COMPLETED_RUN_FILE = "/home/lds00/sprinkler/last_completed_run.json"
WATERING_HISTORY_JSONL = "/home/lds00/sprinkler/watering_history.jsonl"
MIST_STATUS_FILE = "/home/lds00/sprinkler/mist_status.json"
SOIL_LOG_PATH = "/home/lds00/sprinkler/soil_readings.log"

app = Flask(__name__)

# Add global state for manual_set and soon_set
manual_set = None
soon_set = None

@app.route("/schedule-index")
def schedule_index():
    try:
        index = get_schedule_day_index()
        now = datetime.now()
        local_time = now.strftime("%Y-%m-%d %H:%M:%S")
        timezone = time.tzname[time.daylight] if time.daylight else time.tzname[0]

        return jsonify({
            "schedule_index": index,
            "local_time": local_time,
            "timezone": timezone
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/stop-all", methods=["POST"])
def stop_all():
    force_stop_all()
    return jsonify({"status": "stopped"})

@app.route("/set-test-mode", methods=["POST"])
def set_test_mode():
    try:
        data = request.get_json(force=True)
        value = data.get("test_mode")
        if value is None:
            return jsonify({"error": "Missing 'test_mode' in request."}), 400
        # Write the new value to test_mode.txt
        with open(TEST_MODE_FILE, "w") as f:
            f.write("1" if value else "0")
        log(f"[API] Test mode set to {value} (file written)")
        # Log file contents and mtime after write
        try:
            with open(TEST_MODE_FILE) as f:
                contents = f.read().strip()
            mtime = os.path.getmtime(TEST_MODE_FILE)
            log(f"[DEBUG] test_mode.txt now: '{contents}', mtime: {mtime}")
        except Exception as e:
            log(f"[DEBUG] Could not read test_mode.txt after write: {e}")
        return jsonify({"status": "ok", "test_mode": value}), 200
    except Exception as e:
        log(f"[ERROR] Failed to set test mode via API: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/status")
def status():
    global manual_set, soon_set
    set_names = ["Hanging Pots", "Garden", "Misters"]
    zones = []
    current_set = CURRENT_RUN.get("Set", "")
    running = CURRENT_RUN.get("Running", False)
    phase = CURRENT_RUN.get("Phase", "")
    for set_name in set_names:
        if running and current_set == set_name:
            status_str = phase or "Watering"
        else:
            status_str = "Idle"
        zones.append({"name": set_name, "status": status_str})
    # Read test mode and timestamp
    try:
        with open(TEST_MODE_FILE) as f:
            test_mode_val = f.read().strip() == "1"
        test_mode_mtime = os.path.getmtime(TEST_MODE_FILE)
    except Exception:
        test_mode_val = False
        test_mode_mtime = None
    led_colors = get_led_colors(current_set, running, test_mode_val, None, manual_set, soon_set, False)

    # --- Current Run Info ---
    if running and current_set:
        # Try to get start_time and duration from CURRENT_RUN, else fallback to now and 0
        start_time = CURRENT_RUN.get("Start_Time")
        if not start_time:
            # Fallback: use now
            start_time = datetime.now().isoformat()
        duration_minutes = CURRENT_RUN.get("Duration_Minutes")
        if not duration_minutes:
            # Fallback: try to get from schedule
            try:
                schedule = load_json("/home/lds00/sprinkler/sprinkler_schedule.json")
                match = next((s for s in schedule.get("sets", []) if s["set_name"] == current_set), None)
                duration_minutes = match.get("run_duration_minutes", 0) if match else 0
            except Exception:
                duration_minutes = 0
        current_run = {
            "set": current_set,
            "start_time": start_time,
            "duration_minutes": duration_minutes,
            "phase": phase,
            "time_remaining_sec": CURRENT_RUN.get("Time_Remaining_Sec", 0),
            "pulse_time_left_sec": CURRENT_RUN.get("Pulse_Time_Left_Sec", 0),
            "soak_remaining_sec": CURRENT_RUN.get("Soak_Remaining_Sec", 0)
        }
    else:
        current_run = None

    # --- Next Run Info ---
    try:
        schedule = load_json("/home/lds00/sprinkler/sprinkler_schedule.json")
        now = datetime.now()
        next_run = None
        days_checked = 0
        max_days = 30
        def get_day_index_for_date(date):
            base = datetime(2023, 12, 31)  # Match scheduler.py (Sunday)
            return (date.date() - base.date()).days % 14
        found = False
        while not found and days_checked < max_days:
            day = now + timedelta(days=days_checked)
            idx = get_day_index_for_date(day)
            if not schedule.get("schedule_days", [False]*14)[idx]:
                days_checked += 1
                continue  # skip non-watering days
            for entry in schedule.get("start_times", []):
                if not entry.get("isEnabled", False):
                    continue
                sched_time = entry["time"]
                set_start_dt = datetime.strptime(f"{day.strftime('%Y-%m-%d')} {sched_time}", "%Y-%m-%d %H:%M")
                if set_start_dt < now and days_checked == 0:
                    continue
                for s in schedule.get("sets", []):
                    if s["set_name"] == "Misters" or not s.get("mode", True):
                        continue
                    duration = s.get("seasonallyAdjustedMinutes") or s.get("run_duration_minutes", 1)
                    pulse = s.get("pulse_duration_minutes")
                    soak = s.get("soak_duration_minutes")
                    if pulse and soak and pulse > 0 and soak > 0:
                        cycles = duration // (pulse + soak)
                        remainder = duration % (pulse + soak)
                        true_minutes = cycles * (pulse + soak) + remainder
                    else:
                        true_minutes = duration
                    if set_start_dt > now or days_checked > 0:
                        next_run = {
                            "set": s["set_name"],
                            "start_time": set_start_dt.isoformat(),
                            "duration_minutes": true_minutes
                        }
                        found = True
                        break
                    set_start_dt += timedelta(minutes=true_minutes)
                if found:
                    break
            days_checked += 1
    except Exception:
        next_run = None

    # --- Last Completed Run ---
    try:
        with open(LAST_COMPLETED_RUN_FILE) as f:
            last_completed_run = json.load(f)
    except Exception:
        last_completed_run = None

    # --- Upcoming Runs List ---
    try:
        N = 10  # Number of upcoming runs to report
        schedule = load_json("/home/lds00/sprinkler/sprinkler_schedule.json")
        now = datetime.now()
        upcoming_runs = []
        days_checked = 0
        max_days = 30  # Prevent infinite loop if schedule_days is all False
        def get_day_index_for_date(date):
            base = datetime(2023, 12, 31)  # Match scheduler.py (Sunday)
            return (date.date() - base.date()).days % 14
        while len(upcoming_runs) < N and days_checked < max_days:
            day = now + timedelta(days=days_checked)
            idx = get_day_index_for_date(day)
            if not schedule.get("schedule_days", [False]*14)[idx]:
                days_checked += 1
                continue  # skip non-watering days
            for entry in schedule.get("start_times", []):
                if not entry.get("isEnabled", False):
                    continue
                sched_time = entry["time"]
                set_start_dt = datetime.strptime(f"{day.strftime('%Y-%m-%d')} {sched_time}", "%Y-%m-%d %H:%M")
                # If today, skip times already passed
                if set_start_dt < now and days_checked == 0:
                    continue
                # Serially schedule each enabled set (except Misters)
                for s in schedule.get("sets", []):
                    if s["set_name"] == "Misters" or not s.get("mode", True):
                        continue
                    duration = s.get("seasonallyAdjustedMinutes") or s.get("run_duration_minutes", 1)
                    # Calculate true run time with pulse/soak if present
                    pulse = s.get("pulse_duration_minutes")
                    soak = s.get("soak_duration_minutes")
                    if pulse and soak and pulse > 0 and soak > 0:
                        # Number of cycles: total duration divided by (pulse+soak)
                        cycles = duration // (pulse + soak)
                        remainder = duration % (pulse + soak)
                        true_minutes = cycles * (pulse + soak) + remainder
                    else:
                        true_minutes = duration
                    if set_start_dt > now or days_checked > 0:
                        upcoming_runs.append({
                            "set": s["set_name"],
                            "start_time": set_start_dt.isoformat(),
                            "duration_minutes": true_minutes
                        })
                    set_start_dt += timedelta(minutes=true_minutes)
                    if len(upcoming_runs) >= N:
                        break
                if len(upcoming_runs) >= N:
                    break
            days_checked += 1
        upcoming_runs = upcoming_runs[:N]
    except Exception:
        upcoming_runs = []

    # --- today_is_watering_day ---
    try:
        from scheduler import should_run_today
        today_is_watering_day = should_run_today(schedule)
    except Exception:
        today_is_watering_day = False

    resp = {
        "system_status": "All Systems Nominal",
        "zones": zones,
        "test_mode": test_mode_val,
        "test_mode_timestamp": test_mode_mtime,
        "led_colors": led_colors,
        "current_run": current_run,
        "next_run": next_run,
        "last_completed_run": last_completed_run,
        "upcoming_runs": upcoming_runs,
        "today_is_watering_day": today_is_watering_day
    }
    return jsonify(resp)

@app.route("/history-log")
def history_log():
    try:
        with open("/home/lds00/sprinkler/watering_history.log", "r") as f:
            return f.read(), 200, {'Content-Type': 'text/plain'}
    except Exception as e:
        return str(e), 500

@app.route("/history")
def history():
    try:
        # Read and filter last 30 days
        cutoff = datetime.now() - timedelta(days=30)
        history = []
        with open(WATERING_HISTORY_JSONL, "r") as f:
            for line in f:
                try:
                    event = json.loads(line)
                    event_dt = datetime.fromisoformat(event["date"])
                    if event_dt >= cutoff:
                        history.append(event)
                except Exception:
                    continue
        return jsonify({"watering_history": history})
    except Exception as e:
        return jsonify({"watering_history": [], "error": str(e)})

# Helper to update mist status (call from mist_manager in main.py)
def update_mist_status(is_misting, last_mist_event, next_mist_event, current_temperature, interval_minutes, duration_minutes):
    data = {
        "is_misting": is_misting,
        "last_mist_event": last_mist_event,
        "next_mist_event": next_mist_event,
        "current_temperature": current_temperature,
        "interval_minutes": interval_minutes,
        "duration_minutes": duration_minutes
    }
    try:
        with open(MIST_STATUS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        pass

@app.route("/mist-status")
def mist_status():
    try:
        with open(MIST_STATUS_FILE) as f:
            data = json.load(f)
        # Add today_is_watering_day to mist-status as well
        try:
            schedule = load_json("/home/lds00/sprinkler/sprinkler_schedule.json")
            from scheduler import should_run_today
            today_is_watering_day = should_run_today(schedule)
        except Exception:
            today_is_watering_day = False
        data["today_is_watering_day"] = today_is_watering_day
        return jsonify(data)
    except Exception:
        # Return default/empty if not available
        return jsonify({
            "is_misting": False,
            "last_mist_event": None,
            "next_mist_event": None,
            "current_temperature": None,
            "interval_minutes": None,
            "duration_minutes": None,
            "today_is_watering_day": False
        })

@app.route("/soil-latest")
def soil_latest():
    try:
        with open(SOIL_LOG_PATH, "r") as f:
            lines = f.readlines()
            if not lines:
                return jsonify({"error": "No soil readings available."}), 404
            last_line = lines[-1]
            # Format: timestamp | {json}
            ts, json_part = last_line.split("|", 1)
            return jsonify({"timestamp": ts.strip(), **json.loads(json_part)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/soil-history")
def soil_history():
    try:
        N = int(request.args.get("n", 100))  # Default: last 100 readings
        readings = []
        with open(SOIL_LOG_PATH, "r") as f:
            for line in f:
                try:
                    ts, json_part = line.split("|", 1)
                    entry = {"timestamp": ts.strip(), **json.loads(json_part)}
                    readings.append(entry)
                except Exception:
                    continue
        return jsonify(readings[-N:])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/soil-data", methods=["POST"])
def soil_data():
    try:
        data = request.get_json(force=True)
        # Log as a single line: timestamp | {json}
        ts = data.get("timestamp", datetime.now().isoformat())
        with open(SOIL_LOG_PATH, "a") as f:
            f.write(f"{ts} | {json.dumps(data)}\n")
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        with open("error_log.txt", "a") as ef:
            ef.write(f"[SOIL-DATA ERROR] {datetime.now().isoformat()} - {str(e)}\n")
        return jsonify({"error": str(e)}), 500

def read_test_mode():
    try:
        with open(TEST_MODE_FILE) as f:
            return f.read().strip() == "1"
    except Exception:
        return False
