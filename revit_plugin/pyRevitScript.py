"""pyRevit Script: AI-BIM Assistant & Code Execution Engine.
Runs directly inside Autodesk Revit via the pyRevit toolbar or script runner.

Workflow:
1. Gathers currently selected Revit elements and their metadata.
2. Prompts user for natural language instruction (e.g. 'Align fire ratings to 2hr' or 'Rename per ISO 19650').
3. Dispatches payload to local pyBIM-LLM FastAPI Gateway (http://localhost:8000/generate-script).
4. Displays generated code and RAG context preview to the user.
5. Executes the generated code inside an active Revit Transaction upon user confirmation.
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

# Autodesk Revit & pyRevit API namespaces (available inside Revit runtime)
try:
    from Autodesk.Revit.DB import Transaction  # type: ignore # noqa: F401
    from pyrevit import forms, script  # type: ignore # noqa: F401
    IN_REVIT = True
except ImportError:
    IN_REVIT = False
    Transaction = None
    forms = None
    script = None

# Backend Gateway URL
GATEWAY_URL = "http://localhost:8000/generate-script"


def get_selected_elements_metadata(uidoc):
    """Extract metadata for elements currently selected by user in Revit UI."""
    metadata = []
    if not uidoc:
        return metadata

    doc = uidoc.Document
    selection_ids = uidoc.Selection.GetElementIds()

    for elem_id in selection_ids:
        elem = doc.GetElement(elem_id)
        if elem:
            cat_name = elem.Category.Name if elem.Category else "Unknown"
            raw_id = getattr(elem_id, "Value", getattr(elem_id, "IntegerValue", None))
            elem_info = {
                "element_id": raw_id if raw_id is not None else int(str(elem_id)),
                "category": cat_name,
                "name": getattr(elem, "Name", ""),
            }
            metadata.append(elem_info)

    return metadata


def call_ai_gateway(prompt, selected_elements, language="python"):
    """Send request to pyBIM-LLM FastAPI Gateway."""
    payload = {
        "user_prompt": prompt,
        "language": language,
        "selected_elements": selected_elements,
        "include_rag_rules": True,
        "temperature": 0.1,
    }

    data = json.dumps(payload).encode("utf-8")
    req = Request(
        GATEWAY_URL,
        data=data,
        headers={"Content-Type": "application/json"},
    )

    try:
        response = urlopen(req, timeout=120)
        resp_json = json.loads(response.read().decode("utf-8"))
        return resp_json
    except URLError as e:
        raise RuntimeError("Failed to reach pyBIM-LLM Gateway at %s. Ensure backend is running.\nError: %s" % (GATEWAY_URL, str(e)))


def execute_generated_code(doc, code_str):
    """Safely execute generated Python script inside a Revit Transaction."""
    # Context dictionary passed to the executed script
    execution_scope = {
        "doc": doc,
        "__builtins__": __builtins__,
    }

    t = None
    try:
        if IN_REVIT and "Transaction" in globals():
            t = Transaction(doc, "AI BIM Script Execution")
            t.Start()
        exec(code_str, execution_scope)
        if t is not None and hasattr(t, "HasStarted") and t.HasStarted() and not t.HasEnded():
            t.Commit()
        return True, "Executed successfully."
    except Exception:
        if t is not None and hasattr(t, "HasStarted") and t.HasStarted() and not t.HasEnded():
            t.RollBack()
        return False, traceback.format_exc()


def main():
    if not IN_REVIT or script is None or forms is None:
        print("This script is designed to run inside Autodesk Revit with pyRevit.")
        return

    try:
        # __revit__ is injected globally by pyRevit during execution
        revit_app = globals().get("__revit__", None)
        if revit_app is None:
            revit_app = __revit__  # type: ignore # noqa: F821
        uidoc = revit_app.ActiveUIDocument
        doc = uidoc.Document
    except (NameError, AttributeError):
        print("Error: __revit__ is not accessible outside active Revit session.")
        return

    # 1. Collect selected elements
    selected_elements = get_selected_elements_metadata(uidoc)
    elem_count = len(selected_elements)

    # 2. Prompt user for instruction
    prompt = forms.ask_for_string(
        prompt="Enter your BIM automation instruction:\n(%d elements selected)" % elem_count,
        title="pyBIM-LLM Assistant",
    )

    if not prompt or not prompt.strip():
        return

    # 3. Call AI Backend
    output = script.get_output()
    output.print_md("### 🤖 pyBIM-LLM: Communicating with AI & RAG Engine...")

    try:
        response = call_ai_gateway(prompt, selected_elements)
    except Exception as e:
        forms.alert(str(e), title="Gateway Error", warn_icon=True)
        return

    code = response.get("code", "")
    rag_sources = response.get("retrieved_sources", [])
    exec_time = response.get("execution_time_seconds", 0)

    sources_str = ", ".join(rag_sources) if rag_sources else "None"
    output.print_md("**⏱️ AI Inference Time:** `%.2fs`" % exec_time)
    output.print_md("**📚 Knowledge Rules Consulted:** `%s`" % sources_str)
    output.print_md("```python\n%s\n```" % code)

    # 4. Confirm before execution
    confirmed = forms.alert(
        "AI has generated the script.\nReview code in output window.\n\nDo you want to execute this in the active model?",
        title="Confirm Execution",
        yes=True,
        no=True,
    )

    if confirmed:
        success, msg = execute_generated_code(doc, code)
        if success:
            forms.alert("Modifications applied successfully!", title="Success")
        else:
            forms.alert("Execution error:\n%s" % msg, title="Execution Failed", warn_icon=True)


if __name__ == "__main__":
    main()
