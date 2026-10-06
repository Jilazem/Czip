"""Portable CLI dispatch; original HKP commands retain their argument handling."""
import runpy
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    route = {'find': 'search', 'find-index': 'index', 'find-drive': 'drive'}
    if command in route:
        sys.argv = [str(root / 'is_kaynagi.py'), route[command], *sys.argv[2:]]
        runpy.run_path(sys.argv[0], run_name='__main__')
    else:
        sys.argv[0] = str(root / 'hkp.py')
        runpy.run_path(sys.argv[0], run_name='__main__')


if __name__ == '__main__':
    main()
