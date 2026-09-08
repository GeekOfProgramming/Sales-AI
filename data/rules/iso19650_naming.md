# ISO 19650 BIM Information Container & File Naming Conventions

## 1. Overview
According to ISO 19650-2, every information container (model file, drawing, document) within the Common Data Environment (CDE) must follow a strictly structured, delimited naming convention.

## 2. Standard Container Naming Schema
The standard naming convention consists of 7 required fields separated by hyphens (`-`):

```
[PROJECT]-[ORIGINATOR]-[VOLUME/SYSTEM]-[LEVEL/LOCATION]-[TYPE]-[ROLE]-[NUMBER]
```

### Fields Definition:
1. **PROJECT (3-6 characters):** Unique alphanumeric project identifier (e.g. `PRJ01`, `HQB02`).
2. **ORIGINATOR (3-6 characters):** Code identifying the creating organization (e.g. `ARCT` for Architect, `STRC` for Structural Engineer, `MEPC` for MEP Consultant).
3. **VOLUME / SYSTEM (2-4 characters):** Logical partition or zone of the asset (e.g. `ZZ` for entire building, `Z1` for Zone 1, `B1` for Block 1).
4. **LEVEL / LOCATION (2 characters):** Vertical level or location (e.g. `00` Ground, `01` Level 1, `02` Level 2, `M1` Mezzanine, `ZZ` Multi-level, `RF` Roof).
5. **TYPE (2 characters):** Information container type:
   - `M3`: 3D Model
   - `M2`: 2D Model
   - `DR`: Drawing
   - `SH`: Schedule
   - `RP`: Report
6. **ROLE (1-2 characters):** Professional discipline role code:
   - `A`: Architectural
   - `S`: Structural
   - `M`: Mechanical
   - `E`: Electrical
   - `P`: Plumbing
   - `B`: Building Management / BIM Coordination
7. **NUMBER (4-5 digits):** Sequential numeric identifier, typically zero-padded (e.g. `0001`, `0002`).

### Example Valid Model File Names:
- `PRJ01-ARCT-ZZ-00-M3-A-0001.rvt`: Project 01, Architect, Whole Building, Ground Floor, 3D Model, Architectural, No. 0001.
- `PRJ01-STRC-Z1-01-M3-S-0002.rvt`: Project 01, Structural Engineer, Zone 1, Level 1, 3D Model, Structural, No. 0002.

## 3. Metadata & Suitability Codes
Each container must have an associated Status/Suitability code:
- `S0`: Work in Progress (WIP) - Internal to originator team.
- `S1`: Suitable for Coordination.
- `S2`: Suitable for Information.
- `S3`: Suitable for Internal Review & Comment.
- `S4`: Suitable for Stage Approval.
- `A1`: Accepted / Authorized for Construction.
