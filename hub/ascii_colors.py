import sys
import datetime

class ASCIIColors:
    RED = "\033[91m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    RESET = "\033[0m"

    @staticmethod
    def red(message):
        """Prints a message to stderr in red."""
        sys.stderr.write(f"{ASCIIColors.RED}{datetime.datetime.now().isoformat()}: {message}{ASCIIColors.RESET}\n")
        sys.stderr.flush()

    @staticmethod
    def yellow(message):
        """Prints a message to stderr in yellow."""
        sys.stderr.write(f"{ASCIIColors.YELLOW}{datetime.datetime.now().isoformat()}: {message}{ASCIIColors.RESET}\n")
        sys.stderr.flush()

    @staticmethod
    def green(message):
        """Prints a message to stderr in green."""
        sys.stderr.write(f"{ASCIIColors.GREEN}{datetime.datetime.now().isoformat()}: {message}{ASCIIColors.RESET}\n")
        sys.stderr.flush()

    @staticmethod
    def cyan(message):
        """Prints a message to stderr in cyan."""
        sys.stderr.write(f"{ASCIIColors.CYAN}{datetime.datetime.now().isoformat()}: {message}{ASCIIColors.RESET}\n")
        sys.stderr.flush()

    @staticmethod
    def magenta(message):
        """Prints a message to stderr in magenta."""
        sys.stderr.write(f"{ASCIIColors.MAGENTA}{datetime.datetime.now().isoformat()}: {message}{ASCIIColors.RESET}\n")
        sys.stderr.flush()