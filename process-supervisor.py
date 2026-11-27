import os
import signal
import subprocess
import sys
import time


children = []
stopping = False


def stop_children(signum=signal.SIGTERM, _frame=None):
    global stopping
    stopping = True
    for child in children:
        if child.poll() is None:
            child.send_signal(signum)


signal.signal(signal.SIGTERM, stop_children)
signal.signal(signal.SIGINT, stop_children)

timekeeper = subprocess.Popen([sys.executable, "manage.py", "run_timekeeper"], env=os.environ.copy())
server = subprocess.Popen(sys.argv[1:], env=os.environ.copy())
children.extend([timekeeper, server])

exit_code = 0
try:
    while True:
        for child in children:
            result = child.poll()
            if result is not None:
                exit_code = result if result else (0 if stopping else 1)
                stop_children()
                raise SystemExit
        time.sleep(0.2)
except SystemExit:
    pass
finally:
    stop_children()
    deadline = time.monotonic() + 10
    for child in children:
        remaining = max(0, deadline - time.monotonic())
        try:
            child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            child.kill()
sys.exit(exit_code)
