# SPDX-License-Identifier: AGPL-3.0-or-later
"""python3 -I -B -m tmcore <command> ... (DESIGN §5.1)"""
import sys

from tmcore.cli import isolate_stdout, main

if __name__ == "__main__":
    sys.exit(main(out=isolate_stdout()))
