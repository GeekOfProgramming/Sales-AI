# Autodesk Revit API: FilteredElementCollector and Filtering Rules

## 1. Principle of Quick vs Slow Filters
Always apply **Quick Filters** (such as class filters and category filters) BEFORE applying **Slow Filters** (such as parameter filters or geometric intersection filters):
1. Quick filters evaluate internal Revit element headers without loading the full element into memory.
2. Slow filters require extracting and calculating full geometry or parameters, which slows performance on large models.

## 2. Common FilteredElementCollector Idioms

### Collect by Category (e.g. Walls, Doors, Rooms):
```python
from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory, ElementCategoryFilter

# Recommended quick filter
walls = (
    FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_Walls)
    .WhereElementIsNotElementType()
    .ToElements()
)
```

### Collect by Class / Type:
```python
from Autodesk.Revit.DB import FilteredElementCollector, FamilyInstance

# Collect all FamilyInstances in the active view
view_id = doc.ActiveView.Id
instances = (
    FilteredElementCollector(doc, view_id)
    .OfClass(FamilyInstance)
    .WhereElementIsNotElementType()
    .ToElements()
)
```

## 3. Reading and Writing Parameters
1. Always check `param.IsReadOnly` before attempting to write using `param.Set(value)`.
2. Determine the storage type using `param.StorageType`:
   - `StorageType.String`: use `param.AsString()` or `param.Set(str)`
   - `StorageType.Double`: internal units are imperial (feet, square feet, cubic feet). Convert from metric when setting.
   - `StorageType.Integer`: use `param.AsInteger()` or `param.Set(int)`
   - `StorageType.ElementId`: use `param.AsElementId()` or `param.Set(ElementId)`
