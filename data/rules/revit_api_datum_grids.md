# Revit API: Grid and Level Creation Guidelines

In Autodesk Revit, Grids and Levels define the primary datum coordinates of a building model.

## 1. Creating Grids Programmatically

To create a linear datum grid in the active document, use the static method `Grid.Create()`:

```python
from Autodesk.Revit.DB import Grid, Line, XYZ, Transaction

# Create a straight line datum
line = Line.CreateBound(XYZ(0, 0, 0), XYZ(100, 0, 0))

# Must be inside an open Transaction
t = Transaction(doc, "Create Structural Grid")
t.Start()
grid = Grid.Create(doc, line)
grid.Name = "Grid-A"
t.Commit()
```

## 2. Creating Levels Programmatically

Levels are created by specifying elevation in internal decimal feet:

```python
Level level = Level.Create(doc, 10.0); // 10.0 feet elevation
level.Name = "Level 2 - First Floor";
```