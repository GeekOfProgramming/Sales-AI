# -*- coding: utf-8 -*-
"""pyBIM AI Assistant: pyRevit Ribbon Command.
Connects directly to the pyBIM-LLM Gateway to generate and execute Revit automation scripts.
"""

import json
import traceback

try:
    # Python 3 (pyRevit CPython engine)
    from urllib.request import Request, urlopen
    from urllib.error import URLError
except ImportError:
    # Python 2 (pyRevit IronPython engine)
    from urllib2 import Request, urlopen, URLError  # type: ignore

try:
    from Autodesk.Revit.DB import Transaction, FilteredElementCollector, BuiltInCategory
    from pyrevit import forms, script
    IN_REVIT = True
except ImportError:
    IN_REVIT = False


def get_gateway_url():
    """Retrieve configured gateway endpoint or use default LAN IP."""
    cfg = script.get_config("pyBIM")
    base_url = cfg.get_option("server_url", "http://10.120.24.34:8000")
    base_url = base_url.rstrip("/")
    return "{}/generate-script".format(base_url)


def get_selected_elements_metadata(uidoc):
    """Collect metadata of elements currently selected by user."""
    metadata = []
    if not uidoc:
        return metadata

    doc = uidoc.Document
    selection_ids = uidoc.Selection.GetElementIds()

    for elem_id in selection_ids:
        elem = doc.GetElement(elem_id)
        if elem:
            cat_name = elem.Category.Name if elem.Category else "Unknown"
            metadata.append({
                "element_id": elem_id.IntegerValue if hasattr(elem_id, "IntegerValue") else int(str(elem_id)),
                "category": cat_name,
                "name": getattr(elem, "Name", ""),
            })

    return metadata


def call_ai_gateway(gateway_url, prompt, selected_elements):
    """Dispatch request to local pyBIM-LLM server."""
    payload = {
        "user_prompt": prompt,
        "language": "python",
        "selected_elements": selected_elements,
        "include_rag_rules": True,
        "temperature": 0.1,
    }

    data = json.dumps(payload).encode("utf-8")
    req = Request(
        gateway_url,
        data=data,
        headers={"Content-Type": "application/json"},
    )

    try:
        response = urlopen(req, timeout=120)
        return json.loads(response.read().decode("utf-8"))
    except URLError as e:
        raise RuntimeError("Cannot reach pyBIM server at {}.\nPlease verify server is running.\nError: {}".format(gateway_url, str(e)))


def execute_generated_code(doc, code_str):
    """Safely execute generated script inside a Revit Transaction."""
    execution_scope = {
        "doc": doc,
        "__builtins__": __builtins__,
    }

    t = None
    try:
        t = Transaction(doc, "pyBIM-LLM Automated Script")
        t.Start()
        exec(code_str, execution_scope)
        if hasattr(t, "HasStarted") and t.HasStarted() and not t.HasEnded():
            t.Commit()
        return True, "Execution succeeded."
    except Exception:
        if t is not None and hasattr(t, "HasStarted") and t.HasStarted() and not t.HasEnded():
            t.RollBack()
        return False, traceback.format_exc()


def main():
    if not IN_REVIT:
        print("This tool is designed to run inside Autodesk Revit with pyRevit.")
        return

    try:
        uidoc = __revit__.ActiveUIDocument  # type: ignore
        doc = uidoc.Document
    except Exception:
        forms.alert("Active Revit document could not be detected.", title="pyBIM Error", warn_icon=True)
        return

    # 1. Gather Selected Elements
    selected_elements = get_selected_elements_metadata(uidoc)
    count = len(selected_elements)

    # 2. Ask user for natural language prompt
    prompt = forms.ask_for_string(
        prompt="Enter your BIM automation instruction:\n({} elements selected)".format(count),
        title="pyBIM AI Assistant",
    )

    if not prompt or not prompt.strip():
        return

    # 3. Call AI Core & RAG
    gateway_url = get_gateway_url()
    output = script.get_output()
    output.print_md("### 🤖 pyBIM-LLM: Consulting Local AI & Knowledge Base...")
    output.print_md("**Target Gateway:** `{}`".format(gateway_url))

    try:
        res = call_ai_gateway(gateway_url, prompt, selected_elements)
    except Exception as e:
        forms.alert(str(e), title="Connection Error", warn_icon=True)
        return

    code = res.get("code", "")
    rag_sources = res.get("retrieved_sources", [])
    duration = res.get("execution_time_seconds", 0)

    output.print_md("**⏱️ Inference Duration:** `{}s` (Model: `{}`)".format(duration, res.get("model_used", "")))
    output.print_md("**📚 Knowledge Applied:** `{}`".format(", ".join(rag_sources) if rag_sources else "Standard Revit API Rules"))
    output.print_md("```python\n{}\n```".format(code))

    # 4. Confirmation Dialog
    confirm = forms.alert(
        "AI has generated the automation script.\nReview code in the output window.\n\nDo you want to execute this in the active document?",
        title="Confirm Execution",
        yes=True,
        no=True,
    )

    if confirm:
        success, msg = execute_generated_code(doc, code)
        if success:
            forms.alert("Modifications applied successfully!", title="Success")
        else:
            forms.alert("Error executing script:\n{}".format(msg), title="Execution Error", warn_icon=True)


if __name__ == "__main__":
    main()
