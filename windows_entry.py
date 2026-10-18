"""Windows portable desktop entry. Loopback server lifetime follows the window."""
import ctypes
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import traceback
import app


def main():
    import webview
    organizer=app.Organizer(app.data_directory())
    token=secrets.token_urlsafe(32)
    server=app.LocalServer(('127.0.0.1',0),app.make_handler(organizer,token))
    window=webview.create_window('拾光 · LightHarbor Windows 1.4.2 Preview',
        f'http://127.0.0.1:{server.server_port}/#{token}',width=1280,height=860,min_size=(860,600))
    closing=threading.Event(); safe_to_close=threading.Event()
    def choose():
        chosen=window.create_file_dialog(webview.FileDialog.FOLDER)
        return chosen[0] if chosen else ''
    organizer.choose_folder=choose
    def shutdown():
        organizer.cancel.set()
        while True:
            with organizer.lock: phase=organizer.state['phase']
            if phase not in ('scanning','preparing','copying','paused'):break
            organizer.cancel.set();time.sleep(.1)
        server.shutdown();server.server_close()
        safe_to_close.set();window.destroy()
    def on_close():
        if safe_to_close.is_set():return True
        if closing.is_set():return False
        with organizer.lock: phase=organizer.state['phase']
        if phase in ('scanning','preparing','copying','paused'):
            if not window.create_confirmation_dialog('退出拾光','当前正在处理照片。停止当前任务并退出？已完成副本会保留。'):return False
        closing.set();threading.Thread(target=shutdown,daemon=True).start()
        return False
    window.events.closing += on_close
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try: webview.start(gui='edgechromium',private_mode=True)
    finally:
        if not safe_to_close.is_set():
            organizer.cancel.set()
            while organizer.state.get('phase') in ('scanning','preparing','copying','paused'):time.sleep(.1)
            server.shutdown();server.server_close()


if __name__=='__main__':
    try:main()
    except Exception:
        log=app.data_directory().parent/'startup-error.txt'
        log.parent.mkdir(parents=True,exist_ok=True)
        log.write_text(traceback.format_exc(),encoding='utf-8')
        ctypes.windll.user32.MessageBoxW(None,
            '拾光启动失败。请确认已安装 Microsoft Edge WebView2 Runtime 和 .NET Framework 4.8。\n详见先读我.txt。\n错误日志：'+str(log),
            '拾光',0x10)
        sys.exit(1)
