# ExporterIFCUtils Class

|  |
| --- |
| Exporter IFCUtils Class |

A class that contains utilities needed to implement Revit's version of the IFC export client application.

Inheritance Hierarchy

System

Object
  
Autodesk.Revit.DB.IFC

ExporterIFCUtils

  
**Namespace:**

Autodesk.Revit.DB.IFC
  
**Assembly:**
RevitAPIIFC (in RevitAPIIFC.dll) Version: 27.0.4.0 (27.0.4.0)

Syntax

C#

VB

C++

F#

```python
public static class ExporterIFCUtils
```

```python
Public NotInheritable Class ExporterIFCUtils
```

```python
public ref class ExporterIFCUtils abstract sealed
```

```python
[<AbstractClassAttribute>]
[<SealedAttribute>]
type ExporterIFCUtils = class end
```

The
ExporterIFCUtils
type exposes the following members.

Methods

|  | Name | Description |
| --- | --- | --- |
|  | AddClippingsToBaseExtrusion |  |
|  | AddValueString | Adds a string value to a built-in parameter. |
|  | AreSolidsEqual | Determines whether two solids are identical, potentially offset from each other. |
|  | CanExportWallGeometryAsExtrusion | Identifies if the base geometry of the wall can be represented as an extrusion. |
|  | CollectGeometryInfo(ExporterIFC, IFCGeometryInfo, GeometryObject, XYZ, Boolean) | Collects all the target geometry from the input geometry object and adds it as IFC handles to the IFCInfo. |
|  | CollectGeometryInfo(ExporterIFC, IFCGeometryInfo, GeometryObject, XYZ, Boolean, Transform) | Collects all the target geometry from the input geometry object and adds it as IFC handles to the IFCInfo. |
|  | ComputeAreaOfCurveLoops |  |
|  | ComputeRoofProjectedArea | Returns the projected area of the room, unscaled. |
|  | ComputeSubcomponents | Splits a roof or floor element composed of planar surfaces into a set of roughly vertical extruded loops of uniform depth if possible. |
|  | CreateAlternateGUID | Creates a GUID for the given element. |
|  | CreateGUID | Creates a randomized GUID. |
|  | CreateProjectLevelGUID | Creates a GUID from Revit project for given GUIDType. |
|  | CreateSubElementGUID | Creates a consistent GUID for an IFC entity related to a Revit element. A "related" sub-element is one that is unique for a given type of element, and can therefore by identified by a simple index value (e.g. PSet\_Wall\_Common property set for a wall.) The index value 0 is reserved, as this would generate the GUID of the element itself. A listing of known sub-elements is contained in IFCSubElementEnums.cs; it is expected that this list would be maintained up-to-date, instead of passing arbitrary values into this function. |
|  | EndExportInternal | Use the internal Revit implementation to relate elements at the end of export. |
|  | ExportExtrudedSlabOpenings | **Obsolete.** |
|  | ExportExtrudedSlabOpeningsAndReturnInfo |  |
|  | ExportSlabAsExtrusion |  |
|  | GetAttachedColumns | Obtains a list of columns known to Revit as intersecting with this wall. |
|  | GetConnectedWalls | Obtains the IFC-specific information regarding the connections between this wall and other elements. |
|  | GetDoor2DArcsFromFamily | Gets the arcs associated with the plan view of a door. |
|  | GetElevationProfile | Obtains the curve loops which bound the wall's elevation profile. |
|  | GetGeometryFromInplaceWall | Obtains a special snapshot of the geometry of an in-place wall element suitable for export. |
|  | GetGlobal2DDirectionHandle | Obtains the handle representing one cardinal direction in 2D. |
|  | GetGlobal2DDirectionHandles | **Obsolete.**   Obtains the handles representing the cardinal directions in 2D. |
|  | GetGlobal2DOriginHandle | Obtains the handle representing the 2D origin. |
|  | GetGlobal3DDirectionHandle | Obtains the handle representing one cardinal direction in 3D. |
|  | GetGlobal3DDirectionHandles | **Obsolete.**   Obtains the handles representing the cardinal directions in 3D. |
|  | GetGlobal3DOriginHandle | Obtains the handle representing the 3D origin. |
|  | GetInstanceCutoutFromWall | Gets the curve loop corresponding to the hole in the wall made by the instance. |
|  | GetLegacyCurtainSubElements | Gets the sub elements from a legacy curtain element. |
|  | GetLegacyStairOrRampComponents | Gets the components of a stair or ramp. |
|  | GetLegacyStairsProperties | Returns one or more properties for legacy (created in R2012 or before) Stairs. |
|  | GetLevelIdByHeight | Gets the level if by the height of the element. |
|  | GetLoopsFromTopBottomFace | Gets the curve loop(s) that represent the bottom or top face of the wall, usable to create an extrusion for the wall geometry. |
|  | GetMinSymbolHeight | Obtains the minimum height of the given FamilySymbol. |
|  | GetMinSymbolWidth | Obtains the minimum width of the given FamilySymbol. |
|  | GetOpeningData | Gets the openings data from the element. |
|  | GetOriginalSymbol | Returns the original family symbol of this family instance, before the instance is modified by joins, cuts, coping, extensions, or other post-processing. |
|  | GetRelativeLocalPlacementOffsetTransform | Obtains the relative transform between two IfcLocalPlacement handles. |
|  | GetRoofComponents | Gets the components of roof slabs. |
|  | GetRoomBoundaryAsCurveLoopArray | Obtains the spatial element boundary curves as an array of CurveLoops, needed for processing into IFC-specific elements later. |
|  | GetTransformForDoorOrWindow | Obtains the transform for the door or window instance. |
|  | GetUnscaledTransform | Obtains the exact unscaled transform from an IfcLocalPlacement handle. |
|  | GetUnscaledTransformWithoutFixOfDirection | **Obsolete.**   Obtains the unscaled transform from an IfcLocalPlacement handle without fix of direction. |
|  | GetWallBaseOffset | Obtains the base offset of the wall. |
|  | GetWallTrimmedCurve | Obtains the curve of the wall trimmed or extended according to the end conditions of the wall. |
|  | HasElevationProfile | Identifies if the wall has a sketched elevation profile. |
|  | IsCurveFromOtherElementSketch | Identifies if the given curve element is generated by another element's sketch, or if it represents an independent curve element accessible directly by the user. |
|  | IsCurveLoopConvexWithOpenings | Checks if the region bounded by the input curve loop can be represented as the subtraction of 0 or more convex polygons from a base convex polygon. |
|  | IsWallBaseRectangular | Identifies if the wall's base can be represented by a direct thickening of the wall's base curve. |
|  | IsWallCompletelyClipped | Determines if the input wall is completely removed by interaction with other elements within the given range. |
|  | IsWallJoinedToTop | Checks if wall is joined to top. |
|  | SetGlobal2DDirectionHandles | Sets the handles representing the cardinal directions in 2D. |
|  | SetGlobal2DOriginHandle | **Obsolete.**   Sets the handle representing the 2D origin. |
|  | SetGlobal3DDirectionHandles | Sets the handles representing the cardinal directions in 3D. |
|  | SetGlobal3DOriginHandle | **Obsolete.**   Sets the handle representing the 3D origin. |
|  | SortCurveLoops |  |
|  | SortFaceEdges | Sorts the edges of a face such that the outer edge loops and their corresponding inner edge loops are separated. |
|  | TransformAndScalePoint | Converts a point from global Revit coordinates to current IFC coordinates, including scale. |
|  | TransformAndScaleVector | Converts a vector from global Revit coordinates to current IFC coordinates, including scale. |
|  | UsesInstanceGeometry | Identifies if the family instance has its own geometry, or uses the symbol's geometry with a transform. |
|  | ValidateCurveLoops |  |

Top

Remarks

This class contains special API utilities needed to enable the implementation of the client application
for IFC export. Some of these utilities will be needed temporarily while the code for IFC export is
migrated into the export client. These temporary interfaces are likely to change in upcoming Revit
releases.

See Also

#### Reference

Autodesk.Revit.DB.IFC Namespace