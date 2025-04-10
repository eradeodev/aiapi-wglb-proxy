# file: config_manager.py
import configparser
from queue import Queue
import threading
import time
from ascii_colors import ASCIIColors

class ConfigManager:
    def __init__(self, config_file, users_file):
        self.config_file = config_file
        self.users_file = users_file
        self._servers = []
        self._authorized_users = {}
        self._thread_lock = threading.Lock()
        self._load_config()
        self._load_users()

    def _load_config(self):
        with self._thread_lock:
            config = configparser.ConfigParser()
            config.read(self.config_file)
            new_servers = []
            current_servers_dict = {name: data for name, data in self._servers}

            for name in config.sections():
                new_url = config[name]["url"]
                new_data = {"url": new_url, "queue": Queue(), "last_processed_time": 0}
                if name in current_servers_dict:
                    old_data = current_servers_dict[name]
                    if old_data["url"] == new_url:
                        new_servers.append((name, old_data))
                    else:
                        new_servers.append((name, {"url": new_url, "queue": old_data["queue"], "last_processed_time": old_data["last_processed_time"]}))
                else:
                    new_servers.append((name, new_data))
            self._servers = new_servers

    def get_servers(self):
        with self._thread_lock:
            return list(self._servers)

    def update_server_process_time(self, server_name):
        with self._thread_lock:
            for name, data in self._servers:
                if name == server_name:
                    data['last_processed_time'] = time.time()
                    break

    def _load_users(self):
        authorized_users = {}
        try:
            with open(self.users_file, "r") as f:
                lines = f.readlines()
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        user, key = line.split(":")
                        authorized_users[user] = key
                    except ValueError:
                        ASCIIColors.red(f"User entry broken: {line}")
        except FileNotFoundError:
            ASCIIColors.yellow(f"User list file not found: {self.users_file}")
        self._authorized_users = authorized_users

    def get_authorized_users(self):
        return self._authorized_users