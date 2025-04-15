import time
from ascii_colors import ASCIIColors
from urllib.parse import urlparse 
import socket
import requests
import traceback

# Backoff schedule in seconds
_BACKOFF_SCHEDULE = [15, 30, 60, 120, 300]

class ReachableServerManager():

    def __init__(self):
        self.config_manager = None
        self.server_logger = None
        self.reachable_servers_array = None

    def _calculate_next_backoff(self, current_backoff_duration):
        """Calculates the next backoff duration based on the schedule."""
        if current_backoff_duration == 0:
            return _BACKOFF_SCHEDULE[0]
        try:
            current_index = _BACKOFF_SCHEDULE.index(current_backoff_duration)
            # If not the last element, get the next one
            if current_index < len(_BACKOFF_SCHEDULE) - 1:
                return _BACKOFF_SCHEDULE[current_index + 1]
            else:
                return _BACKOFF_SCHEDULE[-1] # Stay at max backoff
        except ValueError:
            # If current duration isn't in schedule, start from the beginning
            return _BACKOFF_SCHEDULE[0]

    def _is_server_reachable(self, server_name, server_config):
        """Checks if a server is reachable."""
        server_url = server_config["url"]
        parsed = urlparse(server_url)
        host = parsed.hostname
        enabled = server_config.get("enabled_for_requests", [])
        if not enabled:
            return False
        first_item = enabled[0]
        port = first_item.split('/')[0]
        port = int(port.strip(":"))

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            try:
                s.connect((host, port))
                return True
            except Exception as e:
                ASCIIColors.yellow(
                    f"Server {server_name} ({host}:{port}) unreachable: {str(e)} "
                )
                return False

    def _verify_post_capability(self, server_name, server_config):
        """
        Verifies POST capability: Tries /ping via POST.
        Returns True if any POST check succeeds, False otherwise.
        """
        _POST_VERIFY_TIMEOUT = (5, 10)

        server_url = server_config["url"]
        enabled = server_config.get("enabled_for_requests", [])

        first_item = enabled[0]
        port = first_item.split('/')[0]
        port = int(port.strip(":"))

        # --- Primary Check: POST ---
        verify_url = f"{server_url}:{port}/ping"

        self.server_logger.log(
            event="post_verify_attempt_show",
            user="proxy_server",
            access="Authorized",
            server=server_name,
            message=f"Attempting POST verification via /ping to {verify_url}",
        )
        try:
            response = requests.post(
                verify_url,
                timeout=_POST_VERIFY_TIMEOUT
            )
            response.raise_for_status()  # Check for 2xx status codes
            ASCIIColors.green(f"POST verification successful for {server_name}.")
            self.server_logger.log(event="post_verify_success_show", user="proxy_server", access="Authorized", server=server_name, response_status=response.status_code, message=f"POST verification successful to {verify_url}")
            return True  # Success

        except requests.exceptions.RequestException as e:
            ASCIIColors.yellow(f"POST verification failed for {server_name}: {e}.")
            self.server_logger.log(event="post_verify_failed_show", user="proxy_server", access="Authorized", server=server_name, response_status=getattr(e.response, 'status_code', 0), error=f"POST verification to {verify_url} failed: {e}", )
            return False
        except Exception as e:
            ASCIIColors.red(f"Unexpected error during POST verification for {server_name}: {e}.")
            traceback.print_exc()
            self.server_logger.log(event="post_verify_error_show", user="proxy_server", access="Authorized", server=server_name, error=f"Unexpected POST error to {verify_url}: {e}", )
            return False # Treat unexpected errors as failure



    def get_reachable_servers(self):
        """Builds mapping of servers sorted by queue size and filtered by network reachability, mapped by request path"""
        reachable = []

        self.config_manager._load_config()  # Ensure config is up-to-date
        servers = self.config_manager.get_servers()
        current_time = time.time()

        for server_name, config in servers:
            try:
                # Initialize backoff fields if they don't exist
                config.setdefault('last_post_verify_fail_time', 0)
                config.setdefault('post_verify_backoff_until', 0)
                config.setdefault('current_post_verify_backoff_duration', 0)

                # --- Backoff Check ---
                if config['post_verify_backoff_until'] > current_time:
                    backoff_remaining = config['post_verify_backoff_until'] - current_time
                    ASCIIColors.magenta(
                        f"Server {server_name} is in POST verify backoff for another {backoff_remaining:.1f}s. Skipping adding to reachable servers."
                    )
                    continue # Skip this server for now

                # --- Standard Checks (if not in backoff or backoff expired) ---
                if self._is_server_reachable(server_name, config):
                    enabled = config.get("enabled_for_requests", [])
                    ASCIIColors.yellow(f"Server {server_name} enabled_for_requests = {enabled} ")
                    if not enabled:
                        continue

                    # Verify POST capability
                    post_verified = self._verify_post_capability(server_name, config)

                    if post_verified:
                        # --- Success: Reset backoff state ---
                        if config['post_verify_backoff_until'] > 0: # Only log/reset if it was in backoff
                            ASCIIColors.green(f"Server {server_name} POST verification successful, resetting backoff state. ")
                            self.server_logger.log(
                                event="server_backoff_reset",
                                user="proxy_server",
                                server=server_name,
                                message="Server POST verification successful, backoff reset."
                            )
                            config['last_post_verify_fail_time'] = 0
                            config['post_verify_backoff_until'] = 0
                            config['current_post_verify_backoff_duration'] = 0
                            # Persist the reset state if config_manager supports it
                            # self.config_manager.update_server_config(server_name, config)

                        # Add server to reachable list
                        reachable.append((server_name, config)) # Append tuple

                    else:
                        # --- Failure: Update backoff state ---
                        ASCIIColors.yellow(
                            f"Server {server_name} failed POST verification, initiating/updating backoff."
                        )
                        new_backoff_duration = self._calculate_next_backoff(config['current_post_verify_backoff_duration'])
                        fail_time = time.time()
                        backoff_until = fail_time + new_backoff_duration

                        config['last_post_verify_fail_time'] = fail_time
                        config['post_verify_backoff_until'] = backoff_until
                        config['current_post_verify_backoff_duration'] = new_backoff_duration

                        self.server_logger.log(
                            event="server_backoff_initiated",
                            user="proxy_server",
                            server=server_name,
                            message=f"Server failed POST verification. Backoff set for {new_backoff_duration}s until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(backoff_until))}.",
                            backoff_duration=new_backoff_duration,
                            backoff_until=backoff_until
                        )
                        # Persist the new backoff state if config_manager supports it
                        # self.config_manager.update_server_config(server_name, config)
                        # Do NOT add to reachable list as POST failed

            except Exception as e:
                ASCIIColors.yellow(
                    f"Error checking server {server_name}: {str(e)}"
                )
                # Log the error appropriately
                self.server_logger.log(
                    event="server_check_error",
                    user="proxy_server",
                    server=server_name,
                    error=f"Failed during reachability/capability check: {e}",
                )

        # Sort reachable servers
        self.reachable_servers_array = sorted(
            reachable,
            key=lambda s: (s[1]["queue"].qsize(), s[1]['last_processed_time'])
        )

    def run(self):
        while True:
            self.get_reachable_servers()
            time.sleep(30) # wait 30 seconds in between refresh of reachable servers

    def get_reachable_servers_by_path(self, path):
        reachable_by_path = []
        for server, config in self.reachable_servers_array:
            enabled = config.get("enabled_for_requests", [])
            if enabled:
                match = [item for item in enabled if path in item]
                if match:
                    reachable_by_path.append((server, config))
        return reachable_by_path
