import sys

class ASCIIColors:
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"

    @staticmethod
    def red(message):
        sys.stderr.write(f"{ASCIIColors.RED}{message}{ASCIIColors.RESET}\n")

    @staticmethod
    def yellow(message):
        sys.stderr.write(f"{ASCIIColors.YELLOW}{message}{ASCIIColors.RESET}\n")
