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

from config_manager import ConfigManager
from proxy_handler import ProxyRequestHandler
from logger import RequestLogger
from ascii_colors import ASCIIColors

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default="config.ini", help="Path to the config file "
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
    request_logger = RequestLogger(args.log_path)
    deactivate_security = args.deactivate_security

    ASCIIColors.red("Ollama Proxy server")

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        pass

    print("Starting server")
    server_address = ("", args.port)
    handler_class = ProxyRequestHandler
    handler_class.config_manager = config_manager
    handler_class.request_logger = request_logger
    handler_class.deactivate_security = deactivate_security

    httpd = ThreadedHTTPServer(server_address, handler_class)
    httpd.socket.settimeout
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