# -*- coding: utf-8 -*-
"""pyBIM Server Configuration: pyRevit Ribbon Command.
Configure Gateway IP address and test connectivity from within Revit.
"""

import json

try:
    from urllib.request import urlopen, Request
    from urllib.error import URLError
except ImportError:
    from urllib2 import urlopen, Request, URLError  # type: ignore

try:
    from pyrevit import forms, script  # type: ignore # noqa: F401
    IN_REVIT = True
except ImportError:
    IN_REVIT = False
    forms = None
    script = None


def main():
    if not IN_REVIT or script is None or forms is None:
        print("This tool is designed to run inside Autodesk Revit with pyRevit.")
        return

    cfg = script.get_config("pyBIM")
    current_url = cfg.get_option("server_url", "http://10.120.24.34:8000")

    new_url = forms.ask_for_string(
        default=current_url,
        prompt="Enter pyBIM-LLM Gateway Server Address:\n(e.g. http://10.120.24.34:8000 or http://localhost:8000)",
        title="pyBIM Server Configuration",
    )

    if not new_url:
        return

    new_url = new_url.strip().rstrip("/")
    health_url = "{}/health".format(new_url)

    # Test connection
    try:
        req = Request(health_url)
        res = urlopen(req, timeout=5)
        data = json.loads(res.read().decode("utf-8"))

        if data.get("status") == "healthy":
            cfg.set_option("server_url", new_url)
            models_list = data.get("available_models") or []
            forms.alert(
                "✅ Connection Successful!\n\n"
                "Server Status: Healthy\n"
                "Ollama Connected: {}\n"
                "BIM Rules Active: {}\n"
                "Models: {}".format(
                    data.get("ollama_connected"),
                    data.get("indexed_rules_count"),
                    ", ".join(models_list),
                ),
                title="Configuration Saved",
            )
        else:
            forms.alert("Server responded with degraded status: {}".format(data), title="Degraded Server", warn_icon=True)
    except Exception as ex:
        forms.alert(
            "❌ Failed to connect to server at:\n{}\n\nError: {}\n\nConfiguration was not saved.".format(health_url, str(ex)),
            title="Connection Failed",
            warn_icon=True,
        )


if __name__ == "__main__":
    main()
