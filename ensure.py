import os
import socket
import subprocess
import sys
from pathlib import Path

PORT = 18791
HERE = Path(__file__).resolve().parent


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


if listening():
    sys.exit(0)

exe = sys.executable
candidate = exe.replace('python.exe', 'pythonw.exe')
if os.path.exists(candidate):
    exe = candidate
DETACHED = 0x00000008
NEW_GROUP = 0x00000200
BREAKAWAY = 0x01000000
flags = DETACHED | NEW_GROUP | BREAKAWAY
try:
    subprocess.Popen([exe, str(HERE / 'server.py')], cwd=str(HERE), creationflags=flags,
                     close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('gateway restarted', flush=True)
except OSError as exc:
    if exc.winerror == 5 and (flags & BREAKAWAY):
        subprocess.Popen([exe, str(HERE / 'server.py')], cwd=str(HERE),
                         creationflags=DETACHED | NEW_GROUP, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print('gateway restarted (without breakaway)', flush=True)
    else:
        raise
