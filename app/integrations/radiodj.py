class RadioDJClient:
    def __init__(self, config):
        self.enabled = config["radiodj"]["enabled"]

    def available(self):
        return self.enabled

    def list_psas(self):
        if not self.enabled:
            raise RuntimeError("RadioDJ disabled")
