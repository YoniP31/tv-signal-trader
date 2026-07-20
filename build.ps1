# Builds a standalone Windows .exe with Nuitka. See README.md "Building a
# standalone .exe" for details and distribution notes.
# --include-package-data=tzdata: the trading-session check needs Israel's
# IANA timezone data bundled in -- Windows has no system tz database, and
# Nuitka doesn't pick up a pure-data package's files automatically.
python -m nuitka --onefile --output-dir=dist --windows-console-mode=force --assume-yes-for-downloads --include-package-data=tzdata --output-filename=tv-signal-trader.exe main.py
