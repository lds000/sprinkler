import os
import signal
import subprocess
import sys

# --- Kill any existing main.py or GPIO-using processes to avoid conflicts ---
# (Removed per user request)

print("[DEBUG] kill_conflicting_processes() complete. Continuing startup...")

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
import RPi.GPIO as GPIO


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
    try:
        spi.open(0, 0)  # bus 0, device 0
        def read_adc(channel):
            adc = spi.xfer2([1, (8 + channel) << 4, 0])
            data = ((adc[1] & 3) << 8) + adc[2]
            return data
        def adc_to_voltage(adc_value, vref=5.0):
            return (adc_value / 1023.0) * vref
        def voltage_to_psi(voltage):
            return (voltage - 0.5) * (100.0 / 4.0)
    except Exception as e:
        log(f"[WARN] SPI device unavailable or failed to open: {e}. Pressure readings will be zero.")
        def read_adc(channel):
            return 0
        def adc_to_voltage(adc_value, vref=5.0):
            return 0.0
        def voltage_to_psi(voltage):
            return 0.0
except ImportError:
    def read_adc(channel):
        return 0
    def adc_to_voltage(adc_value, vref=5.0):
        return 0.0
    def voltage_to_psi(voltage):
        return 0.0

# --- FLOW METER SETUP ---
FLOW_SENSOR_PIN = 22  # Example GPIO pin, change as needed
FLOW_PULSES_PER_LITRE = 450  # Typical for G1"; check your sensor's datasheet

GPIO.setmode(GPIO.BCM)
try:
    GPIO.setup(FLOW_SENSOR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
except Exception as e:
    print(f"[ERROR] Could not set up GPIO pin {FLOW_SENSOR_PIN}: {e}")
    print("If you see 'GPIO busy', make sure no other sprinkler/main.py or GPIO-using process is running.\n" 
          "Stop the systemd service with 'sudo systemctl stop sprinkler' and try again.")
    sys.exit(1)

flow_pulse_count = 0
flow_lock = threading.Lock()

def flow_pulse_callback(channel):
    global flow_pulse_count
    with flow_lock:
        flow_pulse_count += 1

GPIO.add_event_detect(FLOW_SENSOR_PIN, GPIO.FALLING, callback=flow_pulse_callback)

def get_and_reset_flow_litres():
    global flow_pulse_count
    with flow_lock:
        pulses = flow_pulse_count
        flow_pulse_count = 0
    litres = pulses / FLOW_PULSES_PER_LITRE
    return litres

def fetch_remote_flow():
    try:
        resp = requests.get("http://100.117.254.20:8000/env-latest", timeout=2)
        data = resp.json()
        # Return all relevant fields for downstream use
        return {
            "flow_litres": data.get("flow_litres"),
            "flow_pulses": data.get("flow_pulses"),
            "temperature": data.get("temperature"),
            "humidity": data.get("humidity"),
            "pressure_kpa": data.get("pressure_kpa"),
            "moisture_b": data.get("moisture_b")
        }
    except Exception as e:
        log(f"[REMOTE FLOW ERROR] {e}")
        return {}

def fetch_remote_moisture():
    try:
        resp = requests.get("http://100.117.254.20:8000/env-history", timeout=2)
        data = resp.json()
        # Assume the latest entry is last in the list
        if isinstance(data, list) and data:
            latest = data[-1]
            return latest.get("moisture_b")
        elif isinstance(data, dict):
            return data.get("moisture_b")
        else:
            return None
    except Exception as e:
        log(f"[REMOTE MOISTURE ERROR] {e}")
        return None

# --- REMOTE SENSOR FETCHING (NEW ENDPOINTS) ---
def fetch_remote_sets():
    try:
        resp = requests.get("http://100.117.254.20:8000/sets-latest", timeout=2)
        data = resp.json()
        return {
            "flow_litres": data.get("flow_litres"),
            "flow_pulses": data.get("flow_pulses"),
            "pressure_kpa": data.get("pressure_kpa")
        }
    except Exception as e:
        log(f"[REMOTE SETS ERROR] {e}")
        return {}

def fetch_remote_plant():
    try:
        resp = requests.get("http://100.117.254.20:8000/plant-latest", timeout=2)
        data = resp.json()
        return {
            "moisture": data.get("moisture"),
            "lux": data.get("lux"),
            "soil_temperature": data.get("soil_temperature")
        }
    except Exception as e:
        log(f"[REMOTE PLANT ERROR] {e}")
        return {}

def fetch_remote_environment():
    try:
        resp = requests.get("http://100.117.254.20:8000/environment-latest", timeout=2)
        data = resp.json()
        return {
            "temperature": data.get("temperature"),
            "humidity": data.get("humidity"),
            "wind_speed": data.get("wind_speed"),
            "barometric_pressure": data.get("barometric_pressure")
        }
    except Exception as e:
        log(f"[REMOTE ENV ERROR] {e}")
        return {}

# --- ENV DATA POSTING ---
def post_env_data(set_name, flow, moisture_b):
    adc_value = read_adc(0)  # Channel 0 for pressure sensor (local only)
    voltage = adc_to_voltage(adc_value)
    pressure = voltage_to_psi(voltage)
    # --- Use new remote endpoints ---
    sets = fetch_remote_sets()
    plant = fetch_remote_plant()
    env = fetch_remote_environment()
    # Use remote values if available, else fallback to passed-in or None
    flow_litres = sets.get("flow_litres") if sets.get("flow_litres") is not None else flow
    flow_lpm = flow_litres * 60 if flow_litres is not None else None
    # Compose payload for GUI/API
    payload = {
        "timestamp": datetime.now().isoformat(),
        "set_name": set_name,
        "pressure": pressure,  # Local pressure sensor (psi)
        "flow": flow_lpm,
        "moisture_b": plant.get("moisture") if plant.get("moisture") is not None else moisture_b
    }
    # Add all new fields if present
    for d in (sets, plant, env):
        for k, v in d.items():
            if v is not None:
                payload[k] = v
    try:
        requests.post("http://127.0.0.1:5000/env-data", json=payload, timeout=2)
    except Exception as e:
        log(f"[ENV_DATA POST ERROR] {e}")

# --- POST REMOTE DATA TO GUI/API AS SEPARATE PAYLOADS ---
def post_all_env_data(set_name=None):
    adc_value = read_adc(0)  # Channel 0 for pressure sensor (local only)
    voltage = adc_to_voltage(adc_value)
    pressure = voltage_to_psi(voltage)
    # Fetch all remote data
    sets = fetch_remote_sets()
    plant = fetch_remote_plant()
    env = fetch_remote_environment()
    # Add set_name and local pressure to sets payload
    if sets is not None:
        sets_payload = dict(sets)
        sets_payload["timestamp"] = datetime.now().isoformat()
        sets_payload["set_name"] = set_name
        sets_payload["pressure"] = pressure  # Local pressure sensor (psi)
        try:
            requests.post("http://127.0.0.1:5000/sets-data", json=sets_payload, timeout=2)
        except Exception as e:
            log(f"[SETS_DATA POST ERROR] {e}")
    if plant is not None:
        plant_payload = dict(plant)
        plant_payload["timestamp"] = datetime.now().isoformat()
        try:
            requests.post("http://127.0.0.1:5000/plant-data", json=plant_payload, timeout=2)
        except Exception as e:
            log(f"[PLANT_DATA POST ERROR] {e}")
    if env is not None:
        env_payload = dict(env)
        env_payload["timestamp"] = datetime.now().isoformat()
        try:
            requests.post("http://127.0.0.1:5000/environment-data", json=env_payload, timeout=2)
        except Exception as e:
            log(f"[ENVIRONMENT_DATA POST ERROR] {e}")

# --- ENV HISTORY LOGGER THREAD ---
def env_history_logger():
    while True:
        time.sleep(300)  # 5 minutes
        set_name = CURRENT_RUN.get("Set") if CURRENT_RUN.get("Running") else None
        post_all_env_data(set_name)

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
    # DO NOT run Flask server here. Run it separately using flask_api.py
    log("[INFO] main.py started without running Flask server. Start flask_api.py separately for API endpoints.")

import signal
import sys

def handle_sigterm(signum, frame):
    print(f"[DEBUG] Received signal {signum}. Exiting.")
    try:
        GPIO.cleanup()
        print("[DEBUG] GPIO cleaned up.")
    except Exception as e:
        print(f"[WARN] GPIO cleanup failed: {e}")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)
