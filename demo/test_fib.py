import sys
import os

# Ensure the script directory is in sys.path to allow importing fibonacci
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

try:
    from fibonacci import fib
    result = fib(10)
    print(result)
except ImportError as e:
    print(f"Error: {e}")
    sys.exit(1)
