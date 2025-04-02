# Entry point wrapper for shiv compatibility.
# Allows `mmrelay:main` to call `main.main()` without moving the script.
# This enables PyZ builds without breaking existing setups that run main.py directly.
def main():
    from main import main as real_main
    real_main()
