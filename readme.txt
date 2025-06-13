Sprinkler Controller: Environmental Data Logging (Pressure & Flow)
===============================================================

Overview
--------
This system logs and reports real-time and historical environmental data (pressure and flow) for a Raspberry Pi-based sprinkler controller. Data is accessible via log files and Flask API endpoints for GUI and analysis.

Pressure Reading (PSI)
---------------------
- **Hardware:** MCP3008 ADC connected to a pressure sensor.
- **Reading:** Analog value is read from the ADC via SPI. If SPI is unavailable or the sensor is missing, the system logs a warning and returns 0.
- **Conversion:** ADC value → voltage → PSI (linear formula).
- **Reporting:** Pressure (in PSI) is included in the payload sent to the Flask API endpoint `/env-data`.

Flow Reading (L/min)
--------------------
- **Hardware:** SENSTREE G1" Hall Effect Water Flow Sensor connected to a GPIO pin.
- **Pulse Counting:** GPIO interrupt counts pulses from the sensor.
- **Conversion:**
    - 450 pulses = 1 litre (per sensor spec).
    - For background logging (every 5 minutes), the code calculates total litres and divides by 5 to get average L/min.
    - When a set is running, the code takes a 2-second pulse sample and calculates real-time flow in L/min.
- **Reporting:** Flow (in L/min) is included in the payload sent to `/env-data`.

Logging & API
-------------
- **Background Thread:** Every 5 minutes, logs the current pressure and average flow (L/min) to `/env-data`.
- **During Watering:** When a set is running, logs real-time pressure and flow (L/min) together.
- **Storage:** The Flask API writes each reading to `/home/lds00/sprinkler/env_readings.log` in a timestamped JSON format.
- **Access:** The log and API endpoints (`/env-history`, `/env-latest`) provide access to historical and latest readings for GUI or analysis.

Typical Log Entry
-----------------
```
2025-06-10T11:00:00 | {"timestamp": "2025-06-10T11:00:00", "set_name": "Test", "pressure": 0, "flow": 0, "moisture_b": 0}
```

Summary
-------
- Both pressure and flow are measured and reported together, either as periodic background data or real-time data during watering.
- All readings are accessible via the log file and API endpoints for further use.

For wiring, sensor specs, and further details, see the code comments in `main.py`.
