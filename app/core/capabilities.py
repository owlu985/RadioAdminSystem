import os
import socket

class Capabilities:
    def __init__(self, config):
        self.config = config
        self.nas_available = False
        self.rest_available = False
        self.radiodj_available = False

    def probe_nas(self):
        path = self.config["nas"]["mount_path"]
        self.nas_available = os.path.ismount(path)

    def probe_rest(self):
        try:
            s = socket.socket()
            s.bind((
                self.config["rest_api"]["host"],
                self.config["rest_api"]["port"]
            ))
            s.close()
            self.rest_available = True
        except OSError:
            self.rest_available = False

    def probe_all(self):
        self.probe_nas()
        self.probe_rest()
