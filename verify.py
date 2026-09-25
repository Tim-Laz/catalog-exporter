#!/usr/bin/env python3
"""Check a finished export:  python3 verify.py [output/<project>]"""

import sys

from catalog_export.verify import main

if __name__ == "__main__":
    main(sys.argv)
