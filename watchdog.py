import ctypes
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PORT = 18791
HERE = Path(__file__).resolve().parent
LOG = HERE / 'watchdog.log'
INTERVAL = 20

MUTEX_NAME = 'Local\\KeySwitchWatchdog'
_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit(0)


def listening():
    sock = socket.socket()
    sock.settimeout(1.5)
    try:
        sock.connect(('127.0.0.1', PORT))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def log(text):
    try:
        if LOG.exists() and LOG.stat().st_size > 51200:
            LOG.unlink()
        with LOG.open('a', encoding='utf-8') as fh:
            fh.write(f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} {text}\n')
    except OSError:
        pass


def spawn_server():
    exe = sys.executable
    candidate = exe.replace('python.exe', 'pythonw.exe')
    if os.path.exists(candidate):
        exe = candidate
    subprocess.Popen([exe, str(HERE / 'server.py')], cwd=str(HERE), close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    log('gateway restarted')


if __name__ == '__main__':
    log('watchdog started')
    while True:
        try:
            if not listening():
                spawn_server()
                time.sleep(3)
        except Exception as exc:
            log(f'watchdog error: {type(exc).__name__}: {exc}')
        time.sleep(INTERVAL)
