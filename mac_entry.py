"""Private stdio lifecycle protocol for the macOS launcher; durable user-confirmed copy plans."""
import json
import secrets
import signal
import sys
import threading
import time
import app


def run():
    organizer = app.Organizer(app.data_directory())
    stopping = threading.Event()
    output_lock = threading.Lock()
    token = secrets.token_urlsafe(32)

    class LifecycleHandler(app.make_handler(organizer, token)):
        def do_POST(self):
            if stopping.is_set():
                return self.send({'error': '应用正在退出，请重新打开后操作'}, 503)
            return super().do_POST()

    server = app.LocalServer(('127.0.0.1', 0), LifecycleHandler)

    def emit(value):
        try:
            with output_lock:
                print(json.dumps(value, ensure_ascii=False), flush=True)
        except (BrokenPipeError, OSError):
            stopping.set()

    def commands():
        for line in sys.stdin:
            if line.strip() == 'shutdown':
                break
        stopping.set()

    def monitor():
        previous = None
        while not stopping.wait(.4):
            with organizer.lock:
                status = {key: organizer.state.get(key) for key in ('phase', 'included', 'completed')}
            if status != previous:
                emit({'event': 'status', **status})
                previous = status
        # Reject subsequent mutations before asking current worker to cancel.
        organizer.cancel.set()
        while True:
            with organizer.lock:
                phase = organizer.state['phase']
            if phase not in ('scanning', 'preparing', 'copying', 'paused'):
                break
            organizer.cancel.set()
            time.sleep(.1)
        server.shutdown()

    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    threading.Thread(target=commands, daemon=True).start()
    threading.Thread(target=monitor, daemon=True).start()
    emit({'event': 'ready', 'url': f'http://127.0.0.1:{server.server_port}/#{token}'})
    try:
        server.serve_forever(poll_interval=.1)
    finally:
        server.server_close()
        emit({'event': 'stopped'})


if __name__ == '__main__':
    run()
