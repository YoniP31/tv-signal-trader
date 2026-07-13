# Builds a standalone Windows .exe with Nuitka. See README.md "Building a
# standalone .exe" for details and distribution notes.
python -m nuitka --onefile --output-dir=dist --windows-console-mode=force --assume-yes-for-downloads --output-filename=tv-signal-trader.exe main.py
