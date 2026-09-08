# -*- coding: utf-8 -*-
"""pyBIM Web Studio Launcher: pyRevit Ribbon Command.
Opens the pyBIM-LLM Web Studio dashboard in the default web browser.
"""

import webbrowser

try:
    from pyrevit import script  # type: ignore # noqa: F401
    IN_REVIT = True
except ImportError:
    IN_REVIT = False
    script = None


def main():
    if IN_REVIT and script is not None:
        cfg = script.get_config("pyBIM")
        base_url = cfg.get_option("server_url", "http://10.120.24.34:8000")
    else:
        base_url = "http://localhost:8000"

    studio_url = "{}/ui".format(base_url.rstrip("/"))
    webbrowser.open(studio_url)


if __name__ == "__main__":
    main()
