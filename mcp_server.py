"""Entry point kept at the repo root for `claude mcp add`; the implementation ships inside the addon."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_bridge"))

from mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
