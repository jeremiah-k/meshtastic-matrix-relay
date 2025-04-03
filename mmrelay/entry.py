# Entry point for PyPI and Shiv
# Delegates to main.main() without moving or duplicating it
def main():
    from main import main as real_main
    real_main()
