import base64
import os
import threading
import logging
from flask import Flask, request, jsonify
import semchunk
import tiktoken
from transformers import AutoTokenizer

# --- Configuration ---
# Maximum concurrent chunking requests allowed per worker process
# Set via environment variable, defaulting to 4
MAX_CONCURRENCY = int(os.environ.get("CHUNKER_MAX_CONCURRENT_THREADS_PER_WORKER", 4))

# --- Globals ---
app = Flask(__name__)
# Semaphore to limit concurrent access to the chunking logic
semaphore = threading.Semaphore(MAX_CONCURRENCY)
# Cache for loaded tokenizers to avoid reloading repeatedly
tokenizer_cache = {}
tokenizer_lock = threading.Lock() # Protects access to tokenizer_cache

# --- Logging Setup ---
logging = logging.getLogger('gunicorn.error')

# --- Helper Functions ---

def get_tokenizer(tokenizer_name_or_path):
    """Loads a tokenizer, caching it for efficiency."""
    with tokenizer_lock:
        if tokenizer_name_or_path not in tokenizer_cache:
            logging.info(f"Loading tokenizer: {tokenizer_name_or_path}")
            
            # first try tiktoken:
            first_exception = None
            try:
                # Parse as Tiktoken encodings
                tokenizer = tiktoken.get_encoding(tokenizer_name_or_path)
                tokenizer_cache[tokenizer_name_or_path] = tokenizer
                return tokenizer
            except Exception as ignored:
                logging.error(f"could not load as tiktoken tokenizer name: {ignored}")
                first_exception = ignored
            
            try:
                tokenizer = AutoTokenizer.from_pretrained(tokenizer_name_or_path)
                tokenizer_cache[tokenizer_name_or_path] = tokenizer
                return tokenizer
            except Exception as e:
                logging.error(f"Failed to load tokenizer {tokenizer_name_or_path}: hf {e}, tiktoken {first_exception}")
                raise ValueError(f"Could not load tokenizer: {tokenizer_name_or_path}") from e
            except Exception as e:
                logging.error(f"Unexpected error during chunker initialization for '{tokenizer_name_or_path}': hf={e}, tiktoken={first_exception}")
                raise RuntimeError("Internal error initializing chunker.") from e
        else:
            return tokenizer_cache[tokenizer_name_or_path]
            

def find_line_number(text, start_index):
    """Finds the 0-based line number for a given character index."""
    if start_index == 0:
        return 0
    # Find the last newline *before* or *at* the start_index
    last_newline = text.rfind('\n', 0, start_index)
    if last_newline == -1:
        # No preceding newline, so it's the first line (index 0)
        return 0
    # Count newlines from the beginning up to the found newline
    return text.count('\n', 0, last_newline + 1) # +1 to include the found newline in the count

def process_chunking_request(data):
    """Handles the core chunking logic for a single request."""
    # --- Parameter Extraction and Validation ---
    base64_text = data.get('text_base64')
    chunk_size = data.get('chunk_size', 256)
    tokenizer_name = data.get('tokenizer', 'o200k_base') # Defaulting here
    overlap = data.get('overlap', 0.0) # Can be float (ratio) if <1 or int (token count) if >=1

    if not base64_text:
        raise ValueError("Missing 'text_base64' field in request JSON.")

    try:
        chunk_size = int(chunk_size)
        if chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer.")
    except ValueError:
        raise ValueError("Invalid 'chunk_size' provided. Must be an integer.")

    try:
        # Allow overlap to be int or float
        overlap = float(overlap) if '.' in str(overlap) else int(overlap)
    except ValueError:
         raise ValueError("Invalid 'overlap' provided. Must be a number (int or float).")

    # --- Decode Text ---
    try:
        text = base64.b64decode(base64_text).decode('utf-8')
    except Exception as e:
        logging.error(f"Base64 decoding failed: {e}")
        raise ValueError("Invalid base64 encoding for 'text_base64'.") from e
    
    # --- Get tokenizer ---
    tokenizer_instance = get_tokenizer(tokenizer_name)
    
    # --- Initialize Chunker ---
    chunker = semchunk.chunkerify(tokenizer_instance, chunk_size)
    if not chunker:
        raise ValueError(f"Failed to initialize chunker with tokenizer/encoding: {tokenizer_name}")

    # --- Perform Chunking ---
    try:
        # Note: semchunk handles int/float overlap automatically if tokenizer known
        chunks, offsets = chunker(text, offsets=True, overlap=overlap)
    except Exception as e:
        logging.error(f"Chunking failed: {e}")
        raise RuntimeError("Internal error during text chunking.") from e

    # --- Format Output ---
    output = []
    for chunk_text, (start, end) in zip(chunks, offsets):
        start_line = find_line_number(text, start)
        token_count = chunker.token_counter(chunk_text)
        chunk_data = {
            "text": chunk_text,
            "start_index": start,
            "end_index": end,
            "token_count": token_count, # More accurate count
            "start_line": start_line    # 0-based line index
        }
        output.append(chunk_data)

    return output

# --- Flask Route ---
@app.route('/api/chunk', methods=['POST'])
def handle_chunk_request():
    """API endpoint to chunk text."""
    if not request.is_json:
        logging.info(f"request wasn't JSON??? == {request}")
        return jsonify({"error": "Request must be JSON"}), 415 # Unsupported Media Type

    data = request.get_json()

    logging.info(f"Received chunking request. Waiting for semaphore (available: {semaphore._value})...")
    acquired = semaphore.acquire(blocking=True) # Blocks until semaphore is available
    if not acquired:
         # Should not happen with blocking=True, but as a safeguard
         logging.error("Failed to acquire semaphore unexpectedly.")
         return jsonify({"error": "Server busy, failed to acquire processing slot."}), 503

    logging.info(f"Semaphore acquired. Processing request. (available: {semaphore._value})")
    try:
        result = process_chunking_request(data)
        return jsonify(result), 200
    except ValueError as e:
        logging.warning(f"Bad Request: {e}")
        return jsonify({"error": f"Bad Request: {str(e)}"}), 400
    except RuntimeError as e:
        logging.error(f"Internal Server Error: {e}")
        return jsonify({"error": f"Internal Server Error: {str(e)}"}), 500
    except Exception as e:
        # Catch-all for unexpected errors during processing
        logging.exception(f"An unexpected error occurred during request processing: {str(e)} ")
        return jsonify({"error": "An unexpected internal server error occurred."}), 500
    finally:
        semaphore.release()
        logging.info(f"Semaphore released. Request finished. (available: {semaphore._value})")

@app.route('/health', methods=['GET'])
def handle_health():
    return '', 200 # Empty response with 200 OK


@app.route('/ping', methods=['GET', 'POST'])
def handle_ping():
    return '', 200 # Empty response with 200 OK
