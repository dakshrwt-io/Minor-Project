"""Allow ``python -m client`` to run the terminal client."""

import sys

from client.terminal import main

if __name__ == "__main__":
    sys.exit(main())