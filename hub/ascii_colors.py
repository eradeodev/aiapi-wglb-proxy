class ASCIIColors:
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"

    @staticmethod
    def red(message):
        print(f"{ASCIIColors.RED}{message}{ASCIIColors.RESET}")

    @staticmethod
    def yellow(message):
        print(f"{ASCIIColors.YELLOW}{message}{ASCIIColors.RESET}")
