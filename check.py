#!/usr/bin/env python3
import subprocess
import sys
from datetime import datetime
import os

# Check if a systemd service is active
def is_service_active(service_name):
    try:
        result = subprocess.run([
            'systemctl', 'is-active', '--quiet', service_name
        ])
        return result.returncode == 0
    except Exception:
        return False

def print_status(msg):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] {msg}")

def print_error_log():
    error_log = "error_log.txt"
    if os.path.exists(error_log):
        print("\n--- Last 10 lines of error_log.txt ---")
        try:
            with open(error_log, "r") as f:
                lines = f.readlines()
                for line in lines[-10:]:
                    print(line.rstrip())
        except Exception as e:
            print(f"Could not read error_log.txt: {e}")
    else:
        print("No error_log.txt found.")

mqtt_ok = is_service_active('mosquitto')
sprinkler_ok = is_service_active('sprinkler')

if mqtt_ok and sprinkler_ok:
    print_status("System Nominal: MQTT broker and sprinkler.service are running.")
elif not mqtt_ok and not sprinkler_ok:
    print_status("ALERT: Both MQTT broker and sprinkler.service are NOT running!")
    print_error_log()
elif not mqtt_ok:
    print_status("ALERT: MQTT broker (mosquitto) is NOT running!")
elif not sprinkler_ok:
    print_status("ALERT: sprinkler.service is NOT running!")
    print_error_log()
else:
    print_status("Unknown system state.")
