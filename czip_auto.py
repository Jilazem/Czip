#!/usr/bin/env python3
"""Pack closed, idle sessions per profile. Never merge or close any source session."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import hkp
from czip_runtime import T, atomic_json, file_lock, read_json


def main():
    import ayar
    if not ayar.oku().get("oto"):
        return 0
    home = Path(os.environ.get('HERMES_HOME', '~/.hermes')).expanduser()
    database = Path(hkp.db_yolu()).resolve()
    if not database.is_file():
        return 0
    cache = home / 'czip' / 'auto.json'
    root = Path(__file__).resolve().parent
    minimum = max(1, int(os.environ.get('CZIP_AUTO_MIN_MESSAGES', '1500')))
    idle = max(0, float(os.environ.get('CZIP_AUTO_IDLE_HOURS', '2'))) * 3600
    maximum = max(1, min(int(os.environ.get('CZIP_AUTO_MAX', '5')), 20))
    with file_lock(cache):
        done = read_json(cache, {})
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute('SELECT id,message_count,last_activity_at FROM sessions '
                'WHERE COALESCE(archived,0)=0 AND COALESCE(hidden,0)=0 AND ended_at IS NOT NULL '
                'AND message_count>=? AND COALESCE(last_activity_at,0)<=? ORDER BY message_count DESC',
                (minimum, time.time() - idle)).fetchall()
        count = 0
        for row in rows:
            key = str(database) + ':' + row['id']
            stamp = [row['message_count'], row['last_activity_at']]
            if done.get(key, {}).get('stamp') == stamp:
                continue
            env = {**os.environ, 'HERMES_STATE_DB': str(database), 'HERMES_HOME':str(home), 'HERMES_SESSION_ID':row['id']}
            for inherited in ('CODEX_THREAD_ID', 'HERMES_SESSION_KEY'):
                env.pop(inherited, None)
            # Exact IDs from this read-only profile query; never "last" or a caller selector.
            result = subprocess.run([sys.executable, str(root/'czip_cli.py'), 'pack', row['id'], '--full', '--json'],
                                    capture_output=True, text=True, env=env, timeout=120)
            if result.returncode:
                raise RuntimeError(T('Background pack failed: ', 'Arka plan paketlemesi başarısız: ') + row['id'])
            packed = json.loads(result.stdout)
            if not packed.get('ok') or packed.get('source_id') != row['id']:
                raise ValueError('Czip background source identity mismatch')
            done[key] = {'stamp': stamp, 'kid': packed['kid'], 'yol': packed['yol']}
            atomic_json(cache, done)
            print(T('Packed ', 'Paketlendi ') + row['id'] + ' -> ' + packed['kid'])
            count += 1
            if count >= maximum:
                break
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
