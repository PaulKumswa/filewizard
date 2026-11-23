"""PyInstaller entry point (both filewizard.exe and filewizardw.exe)."""
import multiprocessing
import sys

from filewizard.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
