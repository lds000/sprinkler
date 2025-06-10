### main.py

import os
import time
import threading
from datetime import datetime
from scheduler import load_json, should_run_today, is_start_time_enabled
from gpio_controller import initialize_gpio, status_led_controller, turn_off
from flask_api import app, manual_set, soon_set
from run_manager import run_set
from status import CURRENT_RUN
from logger import log
import logging
from config import RELAYS
import requests
import json


# NOTE: The Pi must NEVER write to test_mode.txt. This file is managed by the PC GUI only.
SCHEDULE_FILE = "/home/lds00/sprinkler/sprinkler_schedule.json"
MANUAL_COMMAND_FILE = "/home/lds00/sprinkler/manual_command.json"
LOG_FILE = "/home/lds00/sprinkler/watering_history.log"
TEST_MODE_FILE = "/home/lds00/sprinkler/test_mode.txt"
MIST_STATUS_FILE = "/home/lds00/sprinkler/mist_status.json"

DEBUG_VERBOSE = os.getenv("DEBUG_VERBOSE", "0") == "1"
_last_test_mode = None  # for change detection

# --- MIST LOGIC ENHANCEMENT ---
# Fetch temperature from OpenWeatherMap
OPENWEATHER_API_KEY = "cf5f2b7705dbc0348d0f8a773d5d2882"
OPENWEATHER_ZIP = "83702"  # Boise ZIP
OPENWEATHER_UNITS = "imperial"
OPENWEATHER_URL = f"https://api.openweathermap.org/data/2.5/weather?zip={OPENWEATHER_ZIP},us&units={OPENWEATHER_UNITS}&appid={OPENWEATHER_API_KEY}"

# --- Weather caching for mist logic ---
_weather_cache = {"temp": None, "timestamp": 0}
WEATHER_CACHE_SECONDS = 300  # 5 minutes

def get_current_temperature():
    now = time.time()
    if _weather_cache["temp"] is not None and now - _weather_cache["timestamp"] < WEATHER_CACHE_SECONDS:
        return _weather_cache["temp"]
    try:
        response = requests.get(OPENWEATHER_URL, timeout=5)
        response.raise_for_status()
        data = response.json()
        temp = data["main"]["temp"]
        _weather_cache["temp"] = temp
        _weather_cache["timestamp"] = now
        return temp
    except Exception as e:
        log(f"[ERROR] Failed to fetch temperature from OpenWeatherMap: {e}")
        return _weather_cache["temp"]  # Return last known temp (may be None)

# Track last mist times for each temperature setting
_last_mist_times = {}

def mist_manager(schedule):
    # Prevent misting if a manual run is active
    if manual_set is not None:
        log("[MIST] Skipping misting because manual run is active.")
        return
    mist_settings = schedule.get("mist", {}).get("temperature_settings", [])
    if not mist_settings:
        return
    current_temp = get_current_temperature()
    if current_temp is None:
        log("[MIST] Skipping misting because temperature is unavailable.")
        return
    now = time.time()
    from datetime import datetime, timedelta
    # Find the highest temp threshold that applies
    active_setting = None
    for setting in sorted(mist_settings, key=lambda s: s.get("temperature", 0), reverse=True):
        if current_temp >= setting.get("temperature", 0):
            active_setting = setting
            break
    interval = active_setting.get("interval") if active_setting else None
    duration = active_setting.get("duration") if active_setting else None
    # Find last and next mist event times
    try:
        with open("/home/lds00/sprinkler/mist_status.json") as f:
            last_status = json.load(f)
            last_mist_event = last_status.get("last_mist_event")
            next_mist_event = last_status.get("next_mist_event")
    except Exception:
        last_mist_event = None
        next_mist_event = None
    # Calculate next mist event time
    if last_mist_event and interval:
        try:
            last_dt = datetime.fromisoformat(last_mist_event)
            next_mist_event = (last_dt + timedelta(minutes=interval)).isoformat()
        except Exception:
            next_mist_event = None
    # If a mist is triggered, update last_mist_event
    mist_triggered = False
    for setting in mist_settings:
        temp_threshold = setting.get("temperature")
        interval = setting.get("interval")  # in minutes
        duration = setting.get("duration")  # in minutes
        if temp_threshold is None or interval is None or duration is None:
            continue
        if current_temp >= temp_threshold:
            key = f"{temp_threshold}_{interval}_{duration}"
            last_time = _last_mist_times.get(key, 0)
            if now - last_time >= interval * 60:
                log(f"[WEATHER] Current temperature from OpenWeatherMap: {current_temp}°F")
                log(f"[MIST] Triggering mist for temp >= {temp_threshold}°F: {duration} min")
                threading.Thread(
                    target=run_set,
                    args=("Misters", duration, RELAYS, LOG_FILE),
                    kwargs={
                        "source": f"MIST_{temp_threshold}",
                        "pulse": None,
                        "soak": None
                    },
                    daemon=True
                ).start()
                _last_mist_times[key] = now
                mist_triggered = True
                last_mist_event = datetime.now().isoformat()
                next_mist_event = (datetime.now() + timedelta(minutes=interval)).isoformat() if interval else None
    # Update mist status for API
    from flask_api import update_mist_status
    update_mist_status(
        is_misting=mist_triggered,
        last_mist_event=last_mist_event,
        next_mist_event=next_mist_event,
        current_temperature=current_temp,
        interval_minutes=interval,
        duration_minutes=duration
    )

def ensure_all_relays_off():
    for name, pin in RELAYS.items():
        try:
            turn_off(pin, name=name)
        except Exception as e:
            log(f"[WARN] Could not turn off pin {pin} at startup: {e}")

def run_manual_command(command, schedule):
    log("[MANUAL] Manual run triggered")
    # Always (re)initialize GPIO before running manual command
    from gpio_controller import initialize_gpio
    initialize_gpio(RELAYS)
    # Interrupt and stop any currently running set(s)
    from run_manager import force_stop_all
    force_stop_all()
    sets = command.get("manual_run", {}).get("sets", [])
    duration = command.get("manual_run", {}).get("duration_minutes", 1)

    for set_name in sets:
        match = next((s for s in schedule.get("sets", []) if s["set_name"] == set_name), None)
        if match:
            log(f"[MANUAL] Starting {set_name} for {duration} min")
            threading.Thread(
                target=run_set,
                args=(set_name, duration, RELAYS, LOG_FILE),
                kwargs={
                    "source": "MANUAL",
                    "pulse": match.get("pulse_duration_minutes"),
                    "soak": match.get("soak_duration_minutes")
                },
                daemon=True
            ).start()

# Only read from test_mode.txt, never write to it!
def read_test_mode_from_file():
    try:
        with open(TEST_MODE_FILE) as f:
            return f.read().strip() == "1"
    except Exception:
        return False

def get_next_scheduled_set(schedule, current_time):
    # Returns the set scheduled to run within the next 10 minutes
    from datetime import datetime, timedelta
    now = datetime.strptime(current_time, "%H:%M")
    for entry in schedule.get("start_times", []):
        if not entry.get("isEnabled", False):
            continue
        sched_time = datetime.strptime(entry["time"], "%H:%M")
        if 0 <= (sched_time - now).total_seconds() <= 600:
            # Find first enabled set (not "Misters")
            for s in schedule.get("sets", []):
                if s["set_name"] != "Misters" and s.get("mode", True):
                    return s["set_name"]
    return None

# Track misting state in memory for API
mist_state = {
    "is_misting": False,
    "last_mist_event": None,
    "next_mist_event": None,
    "current_temperature": None,
    "interval_minutes": None,
    "duration_minutes": None
}

def update_mist_status(is_misting, last_mist_event, next_mist_event, current_temperature, interval_minutes, duration_minutes):
    mist_state.update({
        "is_misting": is_misting,
        "last_mist_event": last_mist_event,
        "next_mist_event": next_mist_event,
        "current_temperature": current_temperature,
        "interval_minutes": interval_minutes,
        "duration_minutes": duration_minutes
    })
    try:
        with open(MIST_STATUS_FILE, "w") as f:
            json.dump(mist_state, f)
    except Exception:
        pass

# Track last scheduled run for each start time (global)
last_scheduled_run = {}

# --- ADC SETUP FOR PRESSURE SENSOR ---
try:
    import spidev
    spi = spidev.SpiDev()
    spi.open(0, 0)  # bus 0, device 0
    def read_adc(channel):
        adc = spi.xfer2([1, (8 + channel) << 4, 0])
        data = ((adc[1] & 3) << 8) + adc[2]
        return data
    def adc_to_voltage(adc_value, vref=5.0):
        return (adc_value / 1023.0) * vref
    def voltage_to_psi(voltage):
        return (voltage - 0.5) * (100.0 / 4.0)
except ImportError:
    def read_adc(channel):
        return 0
    def adc_to_voltage(adc_value, vref=5.0):
        return 0.0
    def voltage_to_psi(voltage):
        return 0.0

# --- ENV DATA POSTING ---
def post_env_data(set_name, flow, moisture_b):
    adc_value = read_adc(0)  # Channel 0 for pressure sensor
    voltage = adc_to_voltage(adc_value)
    pressure = voltage_to_psi(voltage)
    payload = {
        "timestamp": datetime.now().isoformat(),
        "set_name": set_name,
        "pressure": pressure,
        "flow": flow,
        "moisture_b": moisture_b
    }
    try:
        requests.post("http://127.0.0.1:5000/env-data", json=payload, timeout=2)
    except Exception as e:
        log(f"[ENV_DATA POST ERROR] {e}")

# --- ENV HISTORY LOGGER THREAD ---
def env_history_logger():
    while True:
        time.sleep(300)  # 5 minutes
        set_name = CURRENT_RUN.get("Set") if CURRENT_RUN.get("Running") else None
        flow = None  # Replace with actual flow reading if available
        moisture_b = None  # Replace with actual moisture reading if available
        post_env_data(set_name, flow, moisture_b)

# --- REALTIME GUI POLLING (for documentation) ---
# The GUI should poll /env-latest every second when a set or misters is running.
# The GUI should poll /env-history every 5 minutes for historical data.

def main_loop():
    global _last_test_mode, manual_set, soon_set
    log("[DEBUG] main_loop has started")
    last_manual_mtime = 0
    _last_test_mode = read_test_mode_from_file()
    log(f"[INFO] TEST_MODE = {_last_test_mode}")
    manual_set = None
    soon_set = None
    error_zones = None
    maintenance = False
    while True:
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        today_str = now.strftime("%Y-%m-%d")
        try:
            schedule = load_json(SCHEDULE_FILE)
        except Exception as e:
            log(f"[ERROR] Failed to load schedule: {e}")
            time.sleep(5)
            continue
        # Manual run detection
        if os.path.exists(MANUAL_COMMAND_FILE):
            mtime = os.path.getmtime(MANUAL_COMMAND_FILE)
            if mtime > last_manual_mtime:
                try:
                    data = load_json(MANUAL_COMMAND_FILE)
                    sets = data.get("manual_run", {}).get("sets", [])
                    manual_set = sets[0] if sets else None
                    run_manual_command(data, schedule)
                except Exception as e:
                    log(f"[ERROR] Failed to parse or execute manual command: {e}")
                finally:
                    try:
                        os.remove(MANUAL_COMMAND_FILE)
                    except Exception as e:
                        log(f"[WARN] Could not delete manual command file: {e}")
                last_manual_mtime = mtime
        else:
            manual_set = None
        # Scheduled soon detection
        soon_set = get_next_scheduled_set(schedule, current_time)

        if should_run_today(schedule):
            for entry in schedule.get("start_times", []):
                sched_time = entry["time"]
                if not entry.get("isEnabled", False):
                    continue
                # Only run if we haven't already run this start_time today
                if last_scheduled_run.get(sched_time) == today_str:
                    continue
                if current_time == sched_time:
                    for s in schedule.get("sets", []):
                        if s["set_name"] == "Misters":
                            continue  # skip Misters for scheduled runs
                        if not s.get("mode", True):
                            continue  # skip inactive sets
                        log(f"[SCHEDULED] Launching set {s['set_name']} at {current_time}")
                        threading.Thread(
                            target=run_set,
                            args=(s["set_name"], s.get("run_duration_minutes", 1), RELAYS, LOG_FILE),
                            kwargs={
                                "pulse": s.get("pulse_duration_minutes"),
                                "soak": s.get("soak_duration_minutes")
                            },
                            daemon=True
                        ).start()
                    last_scheduled_run[sched_time] = today_str
        else:
            # Not a watering day, skip scheduling
            pass

        # Enhanced mist logic: use temperature_settings
        mist_manager(schedule)

        current_test_mode = read_test_mode_from_file()
        if current_test_mode != _last_test_mode:
            log(f"[INFO] TEST_MODE changed to {current_test_mode}")
            _last_test_mode = current_test_mode

        time.sleep(1)

def led_status_thread():
    while True:
        test_mode = read_test_mode_from_file()
        # Use CURRENT_RUN, test_mode, manual_set, soon_set, error_zones, maintenance
        status_led_controller(CURRENT_RUN, test_mode=test_mode)
        time.sleep(0.1)

if __name__ == "__main__":
    # --- Port 5000 check and prompt (move to very top) ---
    import socket
    import subprocess
    def is_port_in_use(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", port)) == 0
    port = 5000
    if is_port_in_use(port):
        print(f"Port {port} is already in use.")
        # Find the process using the port
        try:
            result = subprocess.check_output(f"lsof -i :{port} -sTCP:LISTEN -t", shell=True).decode().strip()
            if result:
                pid = result.split("\n")[0]
                answer = input(f"Process {pid} is using port {port}. Kill it? [y/N]: ").strip().lower()
                if answer == 'y':
                    subprocess.run(["kill", "-9", pid])
                    print(f"Killed process {pid} on port {port}.")
                    time.sleep(1)
                else:
                    print("Exiting due to port conflict.")
                    exit(1)
        except Exception as e:
            print(f"Could not determine process using port {port}: {e}")
            exit(1)
    # Now safe to initialize GPIO and start threads
    initialize_gpio(RELAYS)
    ensure_all_relays_off()
    # Clear any manual runs at startup
    try:
        os.remove(MANUAL_COMMAND_FILE)
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"[WARN] Could not delete manual command file at startup: {e}")
    log("[DEBUG] Waiting 2 seconds after ensure_all_relays_off to avoid relay chatter at startup.")
    time.sleep(2)
    threading.Thread(target=main_loop, daemon=True).start()
    threading.Thread(target=led_status_thread, daemon=True).start()
    threading.Thread(target=env_history_logger, daemon=True).start()
    # Suppress Flask/Werkzeug request logs
    import logging as py_logging
    py_logging.getLogger('werkzeug').setLevel(py_logging.WARNING)
    app.run(host="0.0.0.0", port=5000)
