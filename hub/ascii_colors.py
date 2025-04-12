import sys

class ASCIIColors:
    # Basic Colors (Bright versions)
    RED = "\033[91m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"  # Added
    CYAN = "\033[96m"   # Added
    MAGENTA = "\033[95m" # Added
    # Reset code
    RESET = "\033[0m"

    @staticmethod
    def red(message):
        """Prints a message to stderr in red."""
        sys.stderr.write(f"{ASCIIColors.RED}{message}{ASCIIColors.RESET}\n")
        sys.stderr.flush() # Ensure immediate output

    @staticmethod
    def yellow(message):
        """Prints a message to stderr in yellow."""
        sys.stderr.write(f"{ASCIIColors.YELLOW}{message}{ASCIIColors.RESET}\n")
        sys.stderr.flush()

    @staticmethod
    def green(message):
        """Prints a message to stderr in green."""
        sys.stderr.write(f"{ASCIIColors.GREEN}{message}{ASCIIColors.RESET}\n")
        sys.stderr.flush()

    @staticmethod
    def cyan(message):
        """Prints a message to stderr in cyan."""
        # Corrected to use CYAN color
        sys.stderr.write(f"{ASCIIColors.CYAN}{message}{ASCIIColors.RESET}\n")
        sys.stderr.flush()

    @staticmethod
    def magenta(message):
        """Prints a message to stderr in magenta."""
        # Corrected to use MAGENTA color
        sys.stderr.write(f"{ASCIIColors.MAGENTA}{message}{ASCIIColors.RESET}\n")
        sys.stderr.flush()