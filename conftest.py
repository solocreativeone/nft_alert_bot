"""Make the project root importable so tests can `import notifier`, etc."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
