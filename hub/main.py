# file: main.py
"""
project: ollama_proxy_server
file: main.py
author: ParisNeo
description: This is a proxy server that adds a security layer to one or multiple ollama servers and routes the requests to the right server in order to minimize the charge of the server.
"""

import argparse
from http.server import HTTPServer
from socketserver import ThreadingMixIn
import time
import threading

from config_manager import ConfigManager
from reachable_server_manager import ReachableServerManager
from proxy_handler import ProxyRequestHandler
from logger import ServerLogger
from ascii_colors import ASCIIColors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default="proxy_config.ini", help="Path to the config file "
    )
    parser.add_argument(
        "--log_path", default="access_log.txt", help="Path to the access log file"
    )
    parser.add_argument(
        "--users_list", default="authorized_users.txt", help="Path to the authorized users list"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port number for the server"
    )
    parser.add_argument(
        "-d", "--deactivate_security", action="store_true", help="Deactivates security"
    )
    args = parser.parse_args()

    config_manager = ConfigManager(args.config, args.users_list)
    server_logger = ServerLogger(args.log_path)
    deactivate_security = args.deactivate_security

    ASCIIColors.cyan("Ollama Proxy server")

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        pass

    print("Starting server")
    server_address = ("", args.port)

    # Setup ReachableServerManager
    reachable_server_manager = ReachableServerManager()
    reachable_server_manager.config_manager = config_manager
    reachable_server_manager.server_logger = server_logger

    # Create and start the thread
    reachable_server_manager_thread = threading.Thread(target=reachable_server_manager.run)
    reachable_server_manager_thread.daemon = True  # makes the thread exit when the main program does
    reachable_server_manager_thread.start()

    handler_class = ProxyRequestHandler
    handler_class.config_manager = config_manager
    handler_class.reachable_server_manager = reachable_server_manager
    handler_class.request_logger = server_logger
    handler_class.deactivate_security = deactivate_security

    httpd = ThreadedHTTPServer(server_address, handler_class)
    print(f"Running server on port {args.port}")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped by user.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        httpd.shutdown()
        httpd.server_close()

if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as e:
            ASCIIColors.red(f"Top-level exception caught: {e}")
            ASCIIColors.yellow("Server will continue running...")
            time.sleep(5) # Wait for a moment before trying to restart