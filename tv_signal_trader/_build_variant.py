"""Which .exe variant this is -- "admin" (full feature set, unchanged from
the app's original behavior) or "user" (restricted, see config.IS_ADMIN_BUILD
for what that changes). build.ps1 overwrites this file's BUILD_VARIANT value
immediately before compiling the "user" variant, then restores it back to
"admin" afterward -- don't edit this by hand for a real build.

Running from source (python main.py) always behaves as "admin" -- that's
this file's default/committed value, and it never changes unless build.ps1
is actively mid-build.
"""

BUILD_VARIANT = "user"