from app.module import create_module
from besdk import run_standalone

if __name__ == "__main__":
    run_standalone(create_module)
