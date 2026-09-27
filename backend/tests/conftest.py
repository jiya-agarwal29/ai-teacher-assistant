import os
import sys

# Make `backend/` importable as top-level modules (rag, models, ...) regardless
# of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
