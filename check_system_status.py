#!/usr/bin/env python3
import subprocess
import sys

# Check if a systemd service is active
def is_service_active(service_name):
    try:
        result = subprocess.run([
            'systemctl', 'is-active', '--quiet', service_name
        ])
        return result.returncode == 0
    except Exception:
        return False

# Check if a process is running by name
def is_process_running(process_name):
    try:
        result = subprocess.run(
            ['pgrep', '-f', process_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return result.returncode == 0
    except Exception:
        return False

mqtt_ok = is_service_active('mosquitto')
main_ok = is_process_running('main.py')

if mqtt_ok and main_ok:
    print("System Nominal: MQTT broker and main.py are running.")
elif not mqtt_ok and not main_ok:
    print("ALERT: Both MQTT broker and main.py are NOT running!")
elif not mqtt_ok:
    print("ALERT: MQTT broker (mosquitto) is NOT running!")
elif not main_ok:
    print("ALERT: main.py is NOT running!")
else:
    print("Unknown system state.")
