# Autodesk Revit API: Transactions and Document Modification Rules

## 1. Golden Rules of Transactions
1. **Never modify a Revit Document outside an active Transaction:**
   Attempting to create, delete, or modify any `Element` or `Parameter` without an open transaction will throw `Autodesk.Revit.Exceptions.InvalidOperationException`.
2. **Always ensure Transactions are committed or rolled back:**
   Never leave an uncommitted transaction open. In Python / pyRevit, wrap the transaction in a try-finally block or commit explicitly. In C#, always enclose in a `using (Transaction tx = new Transaction(doc, "Name")) { ... }`.
3. **Only one Transaction can be open on a Document at any time:**
   Concurrent nested transactions are forbidden. Use `SubTransaction` or `TransactionGroup` when grouping is necessary.

## 2. Standard Transaction Patterns

### Python (pyRevit) Pattern:
```python
from Autodesk.Revit.DB import Transaction

doc = __revit__.ActiveUIDocument.Document

t = Transaction(doc, "Modify BIM Element")
t.Start()
try:
    # Perform element modifications here
    t.Commit()
except Exception as ex:
    if t.HasStarted() and not t.HasEnded():
        t.RollBack()
    raise ex
```

### C# (.NET) Pattern:
```csharp
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

[Transaction(TransactionMode.Manual)]
public class ModifyElementsCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
    {
        Document doc = commandData.Application.ActiveUIDocument.Document;

        using (Transaction tx = new Transaction(doc, "Modify Elements"))
        {
            tx.Start();
            try
            {
                // Modifications
                tx.Commit();
                return Result.Succeeded;
            }
            catch (Exception ex)
            {
                if (tx.HasStarted() && !tx.HasEnded())
                    tx.RollBack();
                message = ex.Message;
                return Result.Failed;
            }
        }
    }
}
```

## 3. TransactionGroups and SubTransactions
- **TransactionGroup:** Used to group multiple sequential transactions into a single Undo item for the end user using `group.Assimilate()`.
- **SubTransaction:** Used inside an already opened `Transaction` to isolate operations that might need independent rollbacks without aborting the parent transaction.
