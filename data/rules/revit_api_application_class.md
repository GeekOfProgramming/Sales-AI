# Application Class

|  |
| --- |
| Application Class |

Represents the Autodesk Revit Application, providing access to documents, options and other application wide data and settings.

Inheritance Hierarchy

System

Object
  
Autodesk.Revit.ApplicationServices

Application
  
Autodesk.Revit.DB.Macros

ApplicationEntryPoint

  
**Namespace:**

Autodesk.Revit.ApplicationServices
  
**Assembly:**
RevitAPI (in RevitAPI.dll) Version: 27.0.4.0 (27.0.4.0)

Syntax

C#

VB

C++

F#

```python
public class Application : IDisposable
```

```python
Public Class Application
	Implements IDisposable
```

```python
public ref class Application : IDisposable
```

```python
type Application = 
    class
        interface IDisposable
    end
```

The
Application
type exposes the following members.

Properties

|  | Name | Description |
| --- | --- | --- |
|  | ActiveAddInId | Retrieves the Id of the currently running external application. |
|  | AllowNavigationDuringRedraw | Checks whether or not the navigation during redraw is enabled, and enable or disable it. |
|  | AllUsersAddinsLocation | The folder location for .addin files for all users. |
|  | AngleTolerance | Angle tolerance. |
|  | BackgroundColor | The background color to use for model views in this session. |
|  | Cities | Returns a set of all the known city locations within Revit. |
|  | Create | Provides an object that can be used to create new instances of Autodesk Revit API objects. |
|  | CurrentRevitServerAccelerator | Current Revit Server accelerator. |
|  | CurrentUserAddinsLocation | The folder location for .addin files for the current user. |
|  | CurrentUsersAddinsDataFolderPath | Path to AddinsData folder for the current user. |
|  | CurrentUsersDataFolderPath | Path to data folder for the current user. |
|  | DefaultIFCProjectTemplate | Full path to the default template file for new IFC project documents. It may be empty, in which case the DefaultProjectTemplate should be used. |
|  | DefaultProjectTemplate | Full path to the default template file for new project documents. |
|  | DefaultViewDiscipline | The view discipline that will be applied to new views by default. |
|  | Documents | Returns a set of documents within Revit. |
|  | ExportIFCCategoryTable | Full path to the file that defines Revit category to IFC entity mappings for IFC export. |
|  | FamilyTemplatePath | Default path for family template files. |
|  | ImportIFCCategoryTable | Full path to the file that defines IFC entity to Revit category mappings for IFC import. |
|  | IsArchitectureEnabled | Checks whether or not the architecture discipline is enabled, and enable or disable it. |
|  | IsConcreteEnabled | Checks whether or not the concrete discipline is enabled, and enable or disable it. |
|  | IsElectricalAnalysisEnabled | Checks whether or not electrical analysis is enabled, and enable or disable it. |
|  | IsElectricalEnabled | Checks whether or not the electrical discipline is enabled, and enable or disable it. |
|  | IsEnergyAnalysisEnabled | Checks whether or not energy analysis is enabled, and enable or disable it. |
|  | IsInfrastructureEnabled | Checks whether or not infrastructure discipline is enabled, and enable or disable it. |
|  | IsLoggedIn | Checks if the user is logged in from this session to their Autodesk account. |
|  | IsMassingEnabled | Checks whether or not the massing and site tools are enabled, and enable or disable them. |
|  | IsMechanicalAnalysisEnabled | Checks whether or not mechanical analysis is enabled, and enable or disable it. |
|  | IsMechanicalEnabled | Checks whether or not the mechanical discipline is enabled, and enable or disable it. |
|  | IsPipingAnalysisEnabled | Checks whether or not piping analysis is enabled, and enable or disable it. |
|  | IsPipingEnabled | Checks whether or not the piping discipline is enabled, and enable or disable it. |
|  | IsPrecastEnabled | Checks whether or not the precast is enabled, and enable or disable it. |
|  | IsRouteAnalysisEnabled | Checks whether or not route analysis is enabled, and enable or disable it. |
|  | IsStructuralAnalysisEnabled | Checks whether or not the structural analysis is enabled, and enable or disable it. |
|  | IsStructureEnabled | Checks whether or not the structure discipline is enabled, and enable or disable it. |
|  | IsSystemsEnabled | Checks whether or not any systems disciplines (mechanical, electrical, or piping) are enabled. |
|  | IsValidObject | Specifies whether the .NET object represents a valid Revit entity. |
|  | Language | The language used in the current session of Revit. |
|  | LoginUserId | The user id of the user currently logged in. The user id will be empty if the user is not logged in. |
|  | MinimumThickness | The minimum thickness allowed in Revit for a variety of geometric constructs. These include blends, extrusions, and wall layers. |
|  | PointCloudsRootPath | Root path for point cloud files. |
|  | Product | The product type for the current session of Revit. |
|  | RecordingJournalFilename | Retrieve the name of the journal file the Revit is currently recording to. |
|  | SharedComponentsLocation | Location of shared components used by Revit. |
|  | SharedParametersFilename | Contains the fully qualified path to a shared parameters file. |
|  | ShortCurveTolerance | The enforced minimum length for any curve created by Revit. |
|  | ShowGraphicalOpenEndsAreaBasedLoadBoundaryDisconnects | Whether or not to show the graphical open ends for Area Based Load Boundary disconnects. |
|  | ShowGraphicalWarningCableTrayConduitDisconnects | Whether or not to show the graphical warnings for CTC disconnects. |
|  | ShowGraphicalWarningDuctDisconnects | Whether or not to show the graphical warnings for duct disconnects. |
|  | ShowGraphicalWarningElectricalDisconnects | Whether or not to show the graphical warnings for Electrical disconnects. |
|  | ShowGraphicalWarningHangerDisconnects | Whether or not to show the graphical warnings for Fabrication Hanger disconnects. |
|  | ShowGraphicalWarningPipeDisconnects | Whether or not to show the graphical warnings for Pipe disconnects. |
|  | SubVersionNumber | The minor version number of Revit |
|  | SystemsAnalysisWorkfilesRootPath | Root path for systems analysis workfiles |
|  | Username | Gets the user name for the current Revit session. |
|  | VersionBuild | Returns the internal build number of the Autodesk Revit application. |
|  | VersionName | Returns the name of the Revit application. |
|  | VersionNumber | Return the primary version of the Revit application. |
|  | VertexTolerance | Vertex tolerance. |

Top

Methods

|  | Name | Description |
| --- | --- | --- |
|  | CopyModel | Copies an existing model to a new file. Overwriting a file of the same name is allowed. |
|  | Dispose | Releases all resources used by the Application |
|  | Equals | Determines whether the specified object is equal to the current object.   (Inherited from Object ) |
|  | ExtractPartAtomFromFamilyFile | Writes a PartAtom XML from the contents of a family file. |
|  | GetAssets | Gets all the Assets of the specified type. |
|  | GetExtendedApiService  TService |  |
|  | GetFailureDefinitionRegistry | Returns the instance of FailureDefinitionRegistry. |
|  | GetHashCode | Serves as the default hash function.   (Inherited from Object ) |
|  | GetLibraryPaths | Returns path information identifying where Revit searches for content. |
|  | GetRevitServerNetworkHosts | Gets the list of all Revit Server Network hosts in current session. |
|  | GetSystemsAnalysisWorkflowNames | Returns names for systems analysis workflows. |
|  | GetSystemsAnalysisWorkflows | Returns name and path information identifying systems analysis workflow files. |
|  | GetType | Gets the Type of the current instance.   (Inherited from Object ) |
|  | GetWorksharingCentralGUID | Gets the worksharing central GUID of the given server-based model. |
|  | IsJournalPlaying | Determines if the application is currently in journal playback mode. |
|  | IsValidThickness | Checks if the input value is valid to be supplied as a thickness (for an extrusion, or blend, or wall layer, or similar geometric construct). |
|  | NewFamilyDocument | New family document, including family, titleblock, and annotation symbol |
|  | NewProjectDocument(String) | New project document |
|  | NewProjectDocument(UnitSystem) | Creates a new project document with no template file specified. |
|  | NewProjectTemplateDocument | New project template document |
|  | OpenDocumentFile(String) | Opens a document from disk. |
|  | OpenDocumentFile(ModelPath, OpenOptions) | Opens a document from disk or cloud. |
|  | OpenDocumentFile(ModelPath, OpenOptions, IOpenFromCloudCallback) | Opens a document from disk or cloud. |
|  | OpenIFCDocument(String) | Opens an IFC document from disk using default options. |
|  | OpenIFCDocument(String, IFCImportOptions) | Opens an IFC document from disk using custom options. |
|  | OpenSharedParameterFile | Enables access to shared parameter groups and definitions that are maintained on disk. |
|  | PurgeReleasedAPIObjects | Explicitly purges all API objects that have been released but are still awaiting to be finalized |
|  | RegisterFailuresProcessor | Replaces Revit's default user interface (if present) with alternative handling for all warnings and errors (including those not generated by your application) for the rest of the Revit session; if your application is not prepared to respond to all warnings and errors, consider use of IFailuresPreprocessor (in your opened Transaction) or the FailuresProcessing event instead of this interface. |
|  | SetLibraryPaths | Sets path information identifying where Revit searches for content. |
|  | SetSystemsAnalysisWorkflows | Sets name and path information identifying systems analysis workflow files. |
|  | ToString | Returns a string that represents the current object.   (Inherited from Object ) |
|  | UpdateRenderAppearanceLibrary | Updates the stored render appearance library, giving the Revit session access to any new RPC content. |
|  | WriteJournalComment | Writes a comment to the Revit journal file. |

Top

Events

|  | Name | Description |
| --- | --- | --- |
|  | ApplicationInitialized | Subscribe to this event to get notified after the Revit application has been initialized |
|  | DocumentChanged | Subscribe to the DocumentChanged event to be notified when Revit document has changed. |
|  | DocumentClosed | Subscribe to the DocumentClosing event to be notified when Revit is just about to close a document. |
|  | DocumentClosing | Subscribe to the DocumentClosing event to be notified when Revit is just about to close a document. |
|  | DocumentCreated | Subscribe to the DocumentCreated event to be notified immediately after Revit has finished creating a new document. |
|  | DocumentCreating | Subscribe to the DocumentCreating event to be notified when Revit is just about to create a new document. |
|  | DocumentOpened | Subscribe to the DocumentOpened event to be notified immediately after Revit has finished opening a document. |
|  | DocumentOpening | Subscribe to the DocumentOpening event to be notified when Revit is just about to open a document. |
|  | DocumentPrinted | Subscribe to the DocumentPrinted event to be notified immediately after Revit has finished printing a view or ViewSet of the document. |
|  | DocumentPrinting | Subscribe to the DocumentPrinting event to be notified when Revit is just about to print a view or ViewSet of the document. |
|  | DocumentReloadedLatest | Subscribe to the DocumentReloadedLatestEventArgs event to be notified immediately after Revit has finished reloading a document with central model. |
|  | DocumentReloadingLatest | Subscribe to the DocumentReloadingLatestEventArgs event to be notified when Revit is just about to reload latest changes from a central model. |
|  | DocumentSaved | Subscribe to the DocumentSaved event to be notified immediately after Revit has finished saving a document. |
|  | DocumentSavedAs | Subscribe to the DocumentSavedAs event to be notified immediately after Revit has finished saving document with a new file name. |
|  | DocumentSaving | Subscribe to the DocumentSaving event to be notified when Revit is just about to save a document. |
|  | DocumentSavingAs | Subscribe to the DocumentSavingAs event to be notified when Revit is just about to save the document with a new file name. |
|  | DocumentSynchronizedWithCentral | Subscribe to the DocumentSynchronizedWithCentral event to be notified immediately after Revit has finished synchronizing a document with central model. |
|  | DocumentSynchronizingWithCentral | Subscribe to the DocumentSynchronizingWithCentral event to be notified when Revit is just about to synchronize a document with central model. |
|  | DocumentWorksharingEnabled | Subscribe to the DocumentWorksharingEnabled event to be notified when a document has become workshared. |
|  | ElementTypeDuplicated | Subscribe to the ElementTypeDuplicated event to be notified immediately after Revit has finished duplicating an element type. |
|  | ElementTypeDuplicating | Subscribe to the ElementTypeDuplicating event to be notified when Revit is just about to duplicate an element type. |
|  | FailuresProcessing | Subscribe to the FailuresProcessing event to be notified when failures are being processed at the end of transaction. |
|  | FamilyLoadedIntoDocument | Subscribe to the FamilyLoadedInto event to be notified after Revit loaded a family into a document. |
|  | FamilyLoadingIntoDocument | Subscribe to the FamilyLoadingInto event to be notified when Revit is just about to load a family into a document. |
|  | FileExported | Subscribe to the FileExported event to be notified immediately after Revit has finished exporting files of formats supported by the API. |
|  | FileExporting | Subscribe to the FileExporting event to be notified when Revit is just about to export files of formats supported by the API. |
|  | FileImported | Subscribe to the FileImported event to be notified immediately after Revit has finished importing a file of format supported by the API. |
|  | FileImporting | Subscribe to the FileImporting event to be notified when Revit is just about to import a file of format supported by the API. |
|  | LinkedResourceOpened | Subscribe to the LinkedResourceOpened event to be notified immediately after Revit has finished opening a linked resource. |
|  | LinkedResourceOpening | Subscribe to the LinkedResourceOpening event to be notified when Revit is just about to open a linked resource. |
|  | ProgressChanged | Subscribe to the ProgressChanged event to be notified when an operation in Revit has progress bar data available. |
|  | ViewExported | Subscribe to the ViewExported event to be notified immediately after Revit has finished exporting a view of the document. |
|  | ViewExporting | Subscribe to the ViewExporting event to be notified when Revit is just about to export a view of the document. |
|  | ViewPrinted | Subscribe to the ViewPrinted event to be notified immediately after Revit has finished printing a view of the document. |
|  | ViewPrinting | Subscribe to the ViewPrinting event to be notified when Revit is just about to print a view of the document. |
|  | ViewsExportedByContext | Subscribe to the ViewsExportedByContext event to be notified immediately after Revit has finished exporting one or more views of the document via an export context by CustomExporter. |
|  | ViewsExportingByContext | Subscribe to the ViewsExportingByContext event to be notified when Revit is just about to export one or more views of the document via an export context by CustomExporter. |
|  | WorksharedOperationProgressChanged | Subscribe to the WorksharedOperationProgressChanged to be notified when progress has changed during Collaboration for Revit's workshared operations: open model and synchronize with central. |

Top

Example

C#

```python
string versionInfo = application.VersionBuild + "\n"
                    + application.VersionName + "\n"
                    + application.VersionNumber;
```

See Also

#### Reference

Autodesk.Revit.ApplicationServices Namespace