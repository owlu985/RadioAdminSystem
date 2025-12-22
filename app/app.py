from core.capabilities import Capabilities
from admin.server import start_admin

def main():
    config = load_config("config.yaml")
    capabilities = Capabilities(config)
    capabilities.probe_all()

    start_recorder(config, capabilities)
    start_admin(config, capabilities)

if __name__ == "__main__":
    main()
