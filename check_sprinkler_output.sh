#!/bin/bash
# Show the last 40 lines of the sprinkler service's terminal output (journal)
sudo journalctl -u sprinkler.service -n 40 -e
