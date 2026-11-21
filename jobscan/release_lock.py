"""python -m jobscan.release_lock: run by `jobscan off` (laptop/scanner_switch.ps1) after it has
killed a scan that was in progress. If that scan was writing to the sheet, its claim on the Lock
tab is left behind; clearing it lets GitHub's next run write without waiting for it to expire."""
import platform

from .sheet import Sheet


def main():
    cleared = Sheet().release_lock_of(platform.node())
    print(f"released the sheet's lock ({cleared})" if cleared else "the sheet wasn't locked by this laptop")


if __name__ == "__main__":
    main()
