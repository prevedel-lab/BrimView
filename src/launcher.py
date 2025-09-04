# launcher.py
import socket
import time
import webview
import multiprocessing
import sys
import os

import panel as pn


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def resource_path(relative_path):
    # For PyInstaller: get temp folder path
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.abspath(relative_path)

pn.extension()
def serve_panel(port):
    # Start Panel server as a subprocess
    index_path = resource_path("./src/index.py")
    pn.serve(index_path, port=port, address='127.0.0.1', show=False)
    

def open_webview(port):
    # Delay to wait for server to start
    time.sleep(2)
    window = webview.create_window("BrimView", f"http://localhost:{port}")
    webview.start(window.maximize, icon="./BrimView.png")

if __name__ == '__main__':
    multiprocessing.freeze_support()
    port = find_free_port()
    multiprocessing.Process(target=serve_panel,  args=(port,), daemon=True).start()
    open_webview(port)
    sys.exit(0)
