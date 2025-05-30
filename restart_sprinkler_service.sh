#!/bin/bash
# Kill and restart the sprinkler systemd service
sudo systemctl stop sprinkler.service
sudo systemctl start sprinkler.service
