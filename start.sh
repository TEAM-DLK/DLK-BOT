#!/usr/bin/env bash
# Simple start script used by Replit and some hosts.
# Make sure this is executable: chmod +x start.sh

set -e

# Install dependencies (idempotent)
pip install --no-cache-dir -r requirements.txt

# Load environment variables from .env if present (for local/test usage)
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Start the bot
python DLK.py
