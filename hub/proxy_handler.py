from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests
import json
import time
from ascii_colors import ASCIIColors
import traceback
import uuid


# @Work @EX TODO support passing token indicating request should be served via runpod
_GENERATE_PATHS = {"/api/generate", "/api/embed", "/api/chat", "/v1/chat/completions"}
_PROXY_TIMEOUT = (60, 3600)  # (connect timeout, read timeout)
_MAX_RETRIES = 3


class ProxyRequestHandler(BaseHTTPRequestHandler):
    config_manager = None
    reachable_server_manager = None
    request_logger = None
    deactivate_security = False

    # --- Instance variables for request context ---
    # These will be set by _get_request_data
    request_path = None
    request_get_params = None
    request_post_data = None
    user = 'unknown' # Initialize user
    active_server_name = 'unset_server'
    active_server_queue_size = -1
    request_uuid = None # Initialize request_uuid

    def _normalize_model_name(self, name):
        """Removes ':latest' suffix from a model name if present."""
        if name and name.endswith(':latest'):
            return name[:-7]
        return name

    def _send_response_code(self, response_code, message=""):
        """Sends only a status code and headers. Logs manually due to early exit context."""
        server_name = getattr(self, "active_server_name", "unset_server")
        queue_size = getattr(self, 'active_server_queue_size', 'N/A')
        server_log_info = f"{server_name}- Queue Size {queue_size}" if server_name != "unset_server" else "unset_server"
        # --- Logging kept manual due to potential lack of full request context ---
        self.request_logger.log(
            event="response_sent_code_only", # Differentiate event slightly
            user=getattr(self, 'user', 'unknown'),
            ip_address=self.client_address[0],
            access="Authorized" if getattr(self, 'user', 'unknown') != 'unknown' else 'Denied',
            server=server_log_info,
            nb_queued_requests_on_server=queue_size if isinstance(queue_size, int) else -1,
            response_status=response_code,
            message=message,
            request_path=getattr(self, 'request_path', self.path), # Try to get path if available
            request_uuid=self.request_uuid
        )
        self.send_response(response_code, message)

    def _send_response(self, response, message=""):
        """Sends a full response based on the requests.Response object. Logging is handled by _log_request_outcome."""

        self.send_response(response.status_code, message)
        for key, value in response.headers.items():
            if key.lower() not in [
                "content-length",
                "transfer-encoding",
                "content-encoding",
            ]:
                self.send_header(key, value)
        self.end_headers()

        try:
            content = response.content
            self.wfile.write(content)
            self.wfile.flush()
        except BrokenPipeError:
            pass
        except Exception as e:
             # Log error during response writing if needed
             ASCIIColors.red(f"Error writing response body: {e} request_uuid = {self.request_uuid}")
             self._log_request_outcome(
                 event="response_write_error",
                 server_name=getattr(self, "active_server_name", "unset_server"),
                 path=getattr(self, 'request_path', 'unknown'),
                 get_params=getattr(self, 'request_get_params', {}),
                 post_data=getattr(self, 'request_post_data', b''),
                 client_ip=self.client_address[0],
                 start_time=None, # No start time available here
                 response=response, # Pass the original response
                 error=f"Error writing response body: {e}",
                 queue_size=getattr(self, 'active_server_queue_size', -1),
                 access="Authorized" if getattr(self, 'user', 'unknown') != 'unknown' else 'Denied', # Use established access
                 request_uuid=self.request_uuid
             )

    def log_message(self, format, *args):
        # Suppress default BaseHTTPRequestHandler logging
        return

    def do_HEAD(self):
        self._handle_request()

    def do_GET(self):
        self._handle_request()

    def do_POST(self):
        self._handle_request()

    def _validate_user(self):
        """Validates the user based on the Authorization header."""
        try:
            auth_header = self.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return False, "unknown"
            token = auth_header.split(" ")[1]
            try:
                user, key = token.split(":")
                if self.config_manager.get_authorized_users().get(user) == key:
                    return True, user
                else:
                    return False, user
            except ValueError:
                return False, token  # Token format is incorrect
        except Exception:
            return False, "unknown"

    def _handle_security(self):
        """Handles the security check for the request."""
        if self.deactivate_security:
            self.user = "anonymous"
            return True
        else:
            authorized, user = self._validate_user()
            self.user = user # Store validated or attempted user
            client_ip, _ = self.client_address
            if authorized:
                return True
            else:
                ASCIIColors.red(f"User '{user}' is not authorized. request_uuid = {self.request_uuid}")
                # --- Logging kept manual due to early exit context, include request_uuid ---
                self.request_logger.log(
                    event="rejected",
                    user=user,
                    ip_address=client_ip,
                    access="Denied",
                    server="None",
                    nb_queued_requests_on_server=-1,
                    error="Authentication failed",
                    request_path=urlparse(self.path).path, # Get path for context
                    request_uuid=self.request_uuid
                )
                self._send_response_code(403, f"User '{user}' is not authorized")
                self.end_headers()
                return False

    def _get_request_data(self):
        """
        Extracts path, GET parameters, and POST data from the request
        and stores them as instance attributes.
        """
        url = urlparse(self.path)
        self.request_path = url.path
        self.request_get_params = parse_qs(url.query) or {}
        self.request_post_data = b'' # Initialize as bytes
        if self.command in ["POST", "PUT"]:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                self.request_post_data = self.rfile.read(content_length)
        # No return needed, data stored on self

    def match_model(self, model, models):
        """Matches requested model against available models (exact, normalized, substring)."""
        matched_model = next((m for m in models if m == model), None)
        normalized_model = self._normalize_model_name(model)
        if not matched_model:
            matched_model = next((m for m in models if self._normalize_model_name(m) == normalized_model), None)
        if not matched_model:
            matched_model = next((m for m in models if normalized_model in self._normalize_model_name(m)), None)
        return matched_model

    # --- Helper Methods ---

    def _log_request_outcome(self, event, server_name, path, get_params, post_data,
                         client_ip, start_time, response=None, error=None,
                         queue_size=-1, access="Authorized", request_uuid=None):
        """Centralized logging for request outcomes."""
        duration = time.time() - start_time if start_time else None
        status = getattr(response, 'status_code', None)

        log_data = {
            "event": event,
            "user": self.user,
            "ip_address": client_ip,
        }

        # Optional fields (only add if non-empty/non-default)
        if access:
            log_data["access"] = access
        if server_name:
            log_data["server"] = server_name
        if queue_size and queue_size != -1:
            log_data["nb_queued_requests_on_server"] = queue_size
        if path:
            log_data["request_path"] = path
        if get_params:
            log_data["request_params"] = get_params
        if status and status != 0:
            log_data["response_status"] = status
        if duration:
            log_data["duration"] = round(duration, 4)
        if error is not None:
            if isinstance(error, requests.exceptions.HTTPError) and error.response is not None:
                log_data["error"] = f"HTTP error {error.response.status_code}: {error}"
                if error.response.status_code:
                    log_data["response_status"] = error.response.status_code
            else:
                log_data["error"] = str(error)
        if request_uuid:
             log_data["request_uuid"] = request_uuid

        self.request_logger.log(**log_data)

    def _decode_post_data(self, post_data):
        """Safely decodes post data bytes to a dictionary."""
        if isinstance(post_data, bytes):
            try:
                post_data_str = post_data.decode("utf-8")
                return json.loads(post_data_str)
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                ASCIIColors.yellow(f"Could not decode post data as JSON: {e} - request_uuid = {self.request_uuid}")
                return {}
        if isinstance(post_data, dict):
            return post_data
        if post_data is None:
            return {}
        if isinstance(post_data, str):
            try:
                return json.loads(post_data)
            except json.JSONDecodeError:
                ASCIIColors.yellow(f"Could not decode string post data as JSON: {post_data} - request_uuid = {self.request_uuid}")
                return {}
        ASCIIColors.yellow(f"Unexpected post_data type: {type(post_data)} - request_uuid = {self.request_uuid}")
        return {}

    def _attempt_request_on_server(self, server_name, config, path, get_params, post_data, client_ip):
        """
        Attempts a single request to a specific server. Returns True if handled, False if retry needed.
        """
        start_time = time.time()
        load_tracker = config.get("queue")
        queue_size = load_tracker.qsize() if load_tracker else -1
        log_event_prefix = "gen" if path in _GENERATE_PATHS else "default"
        is_generate_path = path in _GENERATE_PATHS
        post_data_dict = {}
        current_post_data = post_data # Keep track of potentially modified post data

        # Log initial attempt (if applicable) and prepare request
        if is_generate_path and load_tracker:
            # Log the *actual* post data being sent (potentially updated)
            self._log_request_outcome(f"{log_event_prefix}_request", server_name, path, get_params, current_post_data, client_ip, None, queue_size=queue_size, request_uuid=self.request_uuid)
            load_tracker.put_nowait(1)

        response = None
        error = None
        request_handled = False # Flag indicates response sent or non-retryable error

        # Execute the request
        try:
            stream = post_data_dict.get("stream", False) if is_generate_path else False
            enabled = config.get("enabled_for_requests", [])
            matching_item = next((item for item in enabled if path in item), None)
            url_to_use = config["url"] + matching_item

            headers = {
                'Content-type':'application/json', 
                'Accept':'application/json'
            }

            response = requests.request(
                self.command,
                url_to_use,
                params=get_params,
                headers=headers,
                data=current_post_data, # Use current (potentially updated) post data
                stream=stream,
                timeout=_PROXY_TIMEOUT,
            )
            response.raise_for_status()

            # Success (2xx)
            self._send_response(response) # Send response
            self.active_server_name = server_name # Record successful server
            self.active_server_queue_size = queue_size # Record queue size at time of success
            self._log_request_outcome(f"{log_event_prefix}_done", server_name, path, get_params, current_post_data, client_ip, start_time, response=response, queue_size=queue_size, request_uuid=self.request_uuid)
            request_handled = True

        # Handle specific exceptions
        except requests.exceptions.HTTPError as e:
            error = e
            response = e.response
            # Log using current_post_data for accuracy
            self._log_request_outcome(f"{log_event_prefix}_error", server_name, path, get_params, current_post_data, client_ip, start_time, response=response, error=error, queue_size=queue_size, request_uuid=self.request_uuid)
            if 400 <= response.status_code < 500:
                error_message = f"{log_event_prefix.capitalize()} request handling failed on {server_name}." # Simpler message
                self._send_response(response, error_message) # Send actual error response
                request_handled = True
            # 5xx errors mean request_handled remains False -> retry

        except requests.exceptions.ConnectionError as e:
            error = e
            ASCIIColors.yellow(f"Could not connect to server {server_name}: {e} - request_uuid = {self.request_uuid}")
            self._log_request_outcome("connection_error", server_name, path, get_params, current_post_data, client_ip, start_time, error=error, queue_size=queue_size, request_uuid=self.request_uuid)

        except requests.exceptions.RequestException as e:
            error = e
            response = getattr(e, 'response', None)
            self._log_request_outcome(f"{log_event_prefix}_error", server_name, path, get_params, current_post_data, client_ip, start_time, response=response, error=error, queue_size=queue_size, request_uuid=self.request_uuid)

        except Exception as e:
            error = e
            ASCIIColors.yellow(f"An unexpected error occurred while routing to {server_name}: {e} - request_uuid = {self.request_uuid}")
            self._log_request_outcome("routing_error", server_name, path, get_params, current_post_data, client_ip, start_time, error=error, queue_size=queue_size, request_uuid=self.request_uuid)

        # Finalize attempt
        finally:
            self.config_manager.update_server_process_time(server_name)
            if is_generate_path and load_tracker:
                try:
                    load_tracker.get_nowait()
                except Exception as q_e:
                    ASCIIColors.red(f"Error updating queue count for {server_name}: {q_e} - request_uuid = {self.request_uuid}")

        return request_handled

    # --- Main Method ---

    def _route_request(self, path, get_params, post_data, reachable_servers):
        """
        Routes the request to a proxy server with retries.
        """
        client_ip, _ = self.client_address

        # --- Standard request routing with retries ---
        attempt = 0
        tried_servers_overall = set()
        overall_start_time = time.time() # Time the whole routing process
        # Use the initially passed reachable_servers for the first attempt
        current_servers_for_attempt = list(reachable_servers) # Make a mutable copy

        while attempt < _MAX_RETRIES:
            attempt += 1
            if not current_servers_for_attempt:
                 ASCIIColors.yellow(f"No reachable servers available for attempt {attempt}. request_uuid = {self.request_uuid}")
                 break # Exit retry loop if no servers left for this attempt

            ASCIIColors.magenta(f"Attempt {attempt}/{_MAX_RETRIES}. Servers available for this attempt: {[s[0] for s in current_servers_for_attempt]} request_uuid = {self.request_uuid}")

            num_servers = len(current_servers_for_attempt)
            servers_to_remove_from_next_attempt = [] # Track servers failing this attempt
            for i in range(num_servers):
                server_index = i # Simple iteration
                server_info = current_servers_for_attempt[server_index]
                server_name, config = server_info
                ASCIIColors.magenta(f"Iterating over servers: server {server_name} - i={i} - server_index={server_index} - request_uuid = {self.request_uuid}")

                # Skip if already tried in *any* previous attempt and we have alternatives
                if server_name in tried_servers_overall and num_servers > 1:
                    continue # Try next server in this attempt first

                tried_servers_overall.add(server_name)
                # active_server context is set within _attempt_request_on_server on success
                ASCIIColors.cyan(f"Attempt {attempt}/{_MAX_RETRIES}: Trying server '{server_name}' for path '{path}'... request_uuid = {self.request_uuid}")
                request_handled = self._attempt_request_on_server(
                    server_name, config, path, get_params, post_data, client_ip
                )

                if request_handled:
                    ASCIIColors.green(f"{path} request successfully handled by server '{server_name}'. request_uuid = {self.request_uuid}")
                    # Success logged within _attempt_request_on_server
                    return # Exit routing function

                # If request was not handled (e.g., 5xx error, connection error)
                ASCIIColors.yellow(f"{path} request attempt on server '{server_name}' failed. Trying next available server or retrying... request_uuid = {self.request_uuid}")
                servers_to_remove_from_next_attempt.append(server_name)
                # Loop continues to the next server in *this* attempt's list

            # After trying all servers in current_servers_for_attempt for this attempt:
            # Prepare the list for the *next* attempt by removing servers that failed irrecoverably
            # or had temporary issues (5xx, connection errors) in this round.
            # A simple approach is to just refresh the list entirely for the next attempt.
            if attempt < _MAX_RETRIES:
                ASCIIColors.magenta(f"End of attempt {attempt}. Refreshing server list for next attempt. request_uuid = {self.request_uuid}")
                # Refresh the list of servers, respecting backoffs etc.
                current_servers_for_attempt = self.reachable_server_manager.get_reachable_servers_by_path(path)
                # Ensure we don't immediately retry servers that just failed in a way
                # that didn't result in a backoff (though most failures should lead to backoff or removal).
                # The get_reachable_servers call inherently handles backoffs.
            else:
                 # Don't refresh if it was the last attempt
                 pass


        # If loop completes without returning, all attempts failed
        all_tried_servers_str = ', '.join(sorted(list(tried_servers_overall)))
        retry_failed_message = f"Failed to process {path} request on any reachable server after {_MAX_RETRIES} attempts. Tried: [{all_tried_servers_str}] - request_uuid = {self.request_uuid}"
        ASCIIColors.red(retry_failed_message)

        # --- Use _log_request_outcome for final routing failure ---
        self._log_request_outcome(
            event="routing_failed_all_attempts",
            server_name="All", # Indicates failure across all tried servers
            path=path,
            get_params=get_params,
            post_data=post_data,
            client_ip=client_ip,
            start_time=overall_start_time, # Log duration for the entire routing attempt
            error=retry_failed_message,
            access="Denied", # Ultimately denied access to service
            request_uuid=self.request_uuid
        )

        # Send 503 Service Unavailable
        self._send_response_code(503, retry_failed_message)
        self.end_headers()


    def _handle_request(self):
        """Main handler for incoming requests. Generates request_uuid."""
        # Generate a unique ID for this request AT THE VERY BEGINNING
        self.request_uuid = str(uuid.uuid4())

        start_time = time.time()
        client_ip, _ = self.client_address
        access_status = "Denied" # Default access status for logging

        try:
            # Security check - sets self.user and potentially exits
            if not self._handle_security():
                return # Exit if security failed

            # If security passed
            access_status = "Authorized" # Update access status for subsequent logs

            # Parse request data and store on self
            self._get_request_data()

            # Find reachable servers for the specific path (respects backoff)
            reachable_servers = self.reachable_server_manager.get_reachable_servers_by_path(self.request_path)

            if not reachable_servers:
                not_available_message = f"No reachable Ollama servers available (or all are in backoff) to handle {self.request_path}. request_uuid = {self.request_uuid}"
                self._send_response_code(503, not_available_message)
                self.end_headers()
                ASCIIColors.red(not_available_message)
                # --- Logging kept manual for this specific early exit ---
                self.request_logger.log(
                    event="error_no_servers",
                    user=self.user,
                    ip_address=client_ip,
                    access=access_status, # Should be Authorized if past security
                    server="None",
                    nb_queued_requests_on_server=-1,
                    error="No reachable Ollama servers (or all in backoff)",
                    request_path=self.request_path,
                    request_params=self.request_get_params,
                    duration = time.time() - start_time, # Log duration up to this point
                    request_uuid=self.request_uuid
                )
                return

            # Route the request using instance attributes
            self._route_request(
                self.request_path,
                self.request_get_params,
                self.request_post_data,
                reachable_servers
            )
            # Outcome (success or final failure) is logged within _route_request or _attempt_request_on_server

        except Exception as e:
            # Catch-all for unexpected errors during request handling (after security/parsing)
            ASCIIColors.red(f"An unexpected error occurred while handling the request: {e} - request_uuid = {self.request_uuid}")
            traceback.print_exc()
            try:
                # Attempt to send 500
                self._send_response_code(500, "Internal Server Error") # Uses self.request_uuid
                self.end_headers()
            except Exception as send_err:
                ASCIIColors.red(f"Failed to send error response to client: {send_err} - request_uuid = {self.request_uuid}")

            # --- Use _log_request_outcome for unexpected errors ---
            self._log_request_outcome(
                event="unexpected_handler_error",
                server_name=getattr(self, 'active_server_name', 'None'), # Log last known server if available
                path=getattr(self, 'request_path', 'unknown'),
                get_params=getattr(self, 'request_get_params', {}),
                post_data=getattr(self, 'request_post_data', b''),
                client_ip=client_ip,
                start_time=start_time, # Log duration until error
                error=f"Unexpected handler error: {e}",
                queue_size=getattr(self, 'active_server_queue_size', -1),
                access=access_status, # Log access status determined earlier
                request_uuid=self.request_uuid
            )