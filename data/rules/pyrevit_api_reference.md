[Skip to content](#pyrevit)

[![logo](../../static/pyRevitLogo.svg)](https://pyrevitlabs.io "pyRevit")



pyRevit

pyrevit





Initializing search

[pyrevitlabs/pyRevit](https://github.com/pyrevitlabs/pyRevit "Go to repository")

* [Home](../..)
* [Developer Notes](../../repo-organization/)
* [Security](../../SECURITY/)
* [Credits](../../CREDITS/)
* [Reference API](./)

[![logo](../../static/pyRevitLogo.svg)](https://pyrevitlabs.io "pyRevit")
pyRevit

[pyrevitlabs/pyRevit](https://github.com/pyrevitlabs/pyRevit "Go to repository")

* [Home](../..)
* Developer Notes



  Developer Notes
  + [Repository organization](../../repo-organization/)
  + [Developer Guide](../../dev-guide/)
  + [Architecture](../../architecture/)
  + [CI/CD and releases](../../ci-cd/)
  + [Adding support for new Revit version](../../adding-revit-version/)
  + [Propose your own custom extension](../../custom_extension/)
  + [Contributing](../../CONTRIBUTING/)
  + [Code Of Conduct](../../CODE_OF_CONDUCT/)
* [Security](../../SECURITY/)
* [Credits](../../CREDITS/)
* Reference API



  Reference API
  + [pyrevit](./)

    pyrevit
    - [\_perf](_perf/)
    - [api](api/)
    - [compat](compat/)
    - [coreutils](coreutils/)

      coreutils
      * [apidocs](coreutils/apidocs/)
      * [appdata](coreutils/appdata/)
      * [applocales](coreutils/applocales/)
      * [assmutils](coreutils/assmutils/)
      * [charts](coreutils/charts/)
      * [colors](coreutils/colors/)
      * [configparser](coreutils/configparser/)
      * [envvars](coreutils/envvars/)
      * [git](coreutils/git/)
      * [logger](coreutils/logger/)
      * [markdown](coreutils/markdown/)

        markdown
        + [\_\_version\_\_](coreutils/markdown/__version__/)
        + [blockparser](coreutils/markdown/blockparser/)
        + [blockprocessors](coreutils/markdown/blockprocessors/)
        + [extensions](coreutils/markdown/extensions/)

          extensions
          - [abbr](coreutils/markdown/extensions/abbr/)
          - [admonition](coreutils/markdown/extensions/admonition/)
          - [attr\_list](coreutils/markdown/extensions/attr_list/)
          - [codehilite](coreutils/markdown/extensions/codehilite/)
          - [def\_list](coreutils/markdown/extensions/def_list/)
          - [extra](coreutils/markdown/extensions/extra/)
          - [fenced\_code](coreutils/markdown/extensions/fenced_code/)
          - [footnotes](coreutils/markdown/extensions/footnotes/)
          - [headerid](coreutils/markdown/extensions/headerid/)
          - [meta](coreutils/markdown/extensions/meta/)
          - [nl2br](coreutils/markdown/extensions/nl2br/)
          - [sane\_lists](coreutils/markdown/extensions/sane_lists/)
          - [smart\_strong](coreutils/markdown/extensions/smart_strong/)
          - [smarty](coreutils/markdown/extensions/smarty/)
          - [tables](coreutils/markdown/extensions/tables/)
          - [toc](coreutils/markdown/extensions/toc/)
          - [wikilinks](coreutils/markdown/extensions/wikilinks/)
        + [inlinepatterns](coreutils/markdown/inlinepatterns/)
        + [odict](coreutils/markdown/odict/)
        + [postprocessors](coreutils/markdown/postprocessors/)
        + [preprocessors](coreutils/markdown/preprocessors/)
        + [serializers](coreutils/markdown/serializers/)
        + [treeprocessors](coreutils/markdown/treeprocessors/)
        + [util](coreutils/markdown/util/)
      * [mathnet](coreutils/mathnet/)
      * [moduleutils](coreutils/moduleutils/)
      * [pyutils](coreutils/pyutils/)
      * [ribbon](coreutils/ribbon/)
      * [yaml](coreutils/yaml/)
    - [engine](engine/)
    - [extensions](extensions/)

      extensions
      * [cacher\_asc](extensions/cacher_asc/)
      * [cacher\_bin](extensions/cacher_bin/)
      * [components](extensions/components/)
      * [extensionmgr](extensions/extensionmgr/)
      * [extpackages](extensions/extpackages/)
      * [genericcomps](extensions/genericcomps/)
      * [parser](extensions/parser/)
    - [forms](forms/)

      forms
      * [\_cpy](forms/_cpy/)
      * [\_ipy](forms/_ipy/)
      * [settings\_window](forms/settings_window/)
      * [toaster](forms/toaster/)
      * [utils](forms/utils/)
    - [framework](framework/)
    - [interop](interop/)

      interop
      * [adc](interop/adc/)
      * [bbx](interop/bbx/)
      * [dxf](interop/dxf/)
      * [ifc](interop/ifc/)
      * [pts](interop/pts/)
      * [rhino](interop/rhino/)
      * [stl](interop/stl/)
      * [xl](interop/xl/)
    - [labs](labs/)
    - [loader](loader/)

      loader
      * [asmmaker](loader/asmmaker/)
      * [hooks](loader/hooks/)
      * [sessioninfo](loader/sessioninfo/)
      * [sessionmgr](loader/sessionmgr/)
      * [systemdiag](loader/systemdiag/)
      * [uimaker](loader/uimaker/)
    - [output](output/)

      output
      * [cards](output/cards/)
      * [linkmaker](output/linkmaker/)
    - [preflight](preflight/)

      preflight
      * [case](preflight/case/)
    - [revit](revit/)

      revit
      * [avf](revit/avf/)
      * [bim360](revit/bim360/)
      * [db](revit/db/)

        db
        + [count](revit/db/count/)
        + [create](revit/db/create/)
        + [delete](revit/db/delete/)
        + [ensure](revit/db/ensure/)
        + [failure](revit/db/failure/)
        + [pickling](revit/db/pickling/)
        + [query](revit/db/query/)
        + [select](revit/db/select/)
        + [transaction](revit/db/transaction/)
        + [update](revit/db/update/)
      * [dc3dserver](revit/dc3dserver/)
      * [events](revit/events/)
      * [features](revit/features/)
      * [files](revit/files/)
      * [geom](revit/geom/)
      * [journals](revit/journals/)
      * [report](revit/report/)
      * [selection](revit/selection/)
      * [serverutils](revit/serverutils/)
      * [tabs](revit/tabs/)
      * [tmpgfx](revit/tmpgfx/)
      * [ui](revit/ui/)
      * [units](revit/units/)
    - [routes](routes/)

      routes
      * [api](routes/api/)
      * [server](routes/server/)

        server
        + [base](routes/server/base/)
        + [exceptions](routes/server/exceptions/)
        + [handler](routes/server/handler/)
        + [router](routes/server/router/)
        + [server](routes/server/server/)
        + [serverinfo](routes/server/serverinfo/)
    - [runtime](runtime/)

      runtime
      * [bundletypemaker](runtime/bundletypemaker/)
      * [dynamotypemaker](runtime/dynamotypemaker/)
      * [invoketypemaker](runtime/invoketypemaker/)
      * [pythontypemaker](runtime/pythontypemaker/)
      * [typemaker](runtime/typemaker/)
      * [types](runtime/types/)
      * [urltypemaker](runtime/urltypemaker/)
    - [script](script/)
    - [telemetry](telemetry/)

      telemetry
      * [events](telemetry/events/)
      * [record](telemetry/record/)
    - [unittests](unittests/)

      unittests
      * [runner](unittests/runner/)
      * [test\_query\_get\_name](unittests/test_query_get_name/)
      * [test\_routes\_server\_response](unittests/test_routes_server_response/)
      * [test\_routes\_server\_unicode](unittests/test_routes_server_unicode/)
      * [test\_upgrade](unittests/test_upgrade/)
    - [userconfig](userconfig/)
    - [versionmgr](versionmgr/)

      versionmgr
      * [about](versionmgr/about/)
      * [updater](versionmgr/updater/)
      * [upgrade](versionmgr/upgrade/)
      * [urls](versionmgr/urls/)

Table of contents

* [pyrevit](#pyrevit)
* [Attributes](#pyrevit-attributes)

  + [PYREVIT\_ADDON\_NAME](#pyrevit.PYREVIT_ADDON_NAME)
  + [PYREVIT\_CLI\_NAME](#pyrevit.PYREVIT_CLI_NAME)
  + [VERSION\_STRING](#pyrevit.VERSION_STRING)
  + [matches](#pyrevit.matches)
  + [BUILD\_METADATA](#pyrevit.BUILD_METADATA)
  + [VERSION\_MAJOR](#pyrevit.VERSION_MAJOR)
  + [VERSION\_MINOR](#pyrevit.VERSION_MINOR)
  + [VERSION\_PATCH](#pyrevit.VERSION_PATCH)
  + [HOME\_DIR](#pyrevit.HOME_DIR)
  + [DOTNET\_RUNTIME\_ID](#pyrevit.DOTNET_RUNTIME_ID)
  + [ROOT\_BIN\_DIR](#pyrevit.ROOT_BIN_DIR)
  + [BIN\_DIR](#pyrevit.BIN_DIR)
  + [MAIN\_LIB\_DIR](#pyrevit.MAIN_LIB_DIR)
  + [MISC\_LIB\_DIR](#pyrevit.MISC_LIB_DIR)
  + [MODULE\_DIR](#pyrevit.MODULE_DIR)
  + [LOADER\_DIR](#pyrevit.LOADER_DIR)
  + [RUNTIME\_DIR](#pyrevit.RUNTIME_DIR)
  + [ADDIN\_DIR](#pyrevit.ADDIN_DIR)
  + [ENGINES\_DIR](#pyrevit.ENGINES_DIR)
  + [PYREVIT\_CLI\_PATH](#pyrevit.PYREVIT_CLI_PATH)
  + [TRACEBACK\_TITLE](#pyrevit.TRACEBACK_TITLE)
  + [HOST\_APP](#pyrevit.HOST_APP)
  + [EXEC\_PARAMS](#pyrevit.EXEC_PARAMS)
  + [DOCS](#pyrevit.DOCS)
  + [USER\_SYS\_TEMP](#pyrevit.USER_SYS_TEMP)
  + [USER\_DESKTOP](#pyrevit.USER_DESKTOP)
  + [EXTENSIONS\_DEFAULT\_DIR](#pyrevit.EXTENSIONS_DEFAULT_DIR)
  + [PYREVIT\_FILE\_PREFIX\_UNIVERSAL](#pyrevit.PYREVIT_FILE_PREFIX_UNIVERSAL)
  + [PYREVIT\_FILE\_PREFIX\_UNIVERSAL\_REGEX](#pyrevit.PYREVIT_FILE_PREFIX_UNIVERSAL_REGEX)
  + [PYREVIT\_FILE\_PREFIX](#pyrevit.PYREVIT_FILE_PREFIX)
  + [PYREVIT\_FILE\_PREFIX\_REGEX](#pyrevit.PYREVIT_FILE_PREFIX_REGEX)
  + [PYREVIT\_FILE\_PREFIX\_STAMPED](#pyrevit.PYREVIT_FILE_PREFIX_STAMPED)
  + [PYREVIT\_FILE\_PREFIX\_STAMPED\_REGEX](#pyrevit.PYREVIT_FILE_PREFIX_STAMPED_REGEX)
  + [PYREVIT\_FILE\_PREFIX\_UNIVERSAL\_USER](#pyrevit.PYREVIT_FILE_PREFIX_UNIVERSAL_USER)
  + [PYREVIT\_FILE\_PREFIX\_UNIVERSAL\_USER\_REGEX](#pyrevit.PYREVIT_FILE_PREFIX_UNIVERSAL_USER_REGEX)
  + [PYREVIT\_FILE\_PREFIX\_USER](#pyrevit.PYREVIT_FILE_PREFIX_USER)
  + [PYREVIT\_FILE\_PREFIX\_USER\_REGEX](#pyrevit.PYREVIT_FILE_PREFIX_USER_REGEX)
  + [PYREVIT\_FILE\_PREFIX\_STAMPED\_USER](#pyrevit.PYREVIT_FILE_PREFIX_STAMPED_USER)
  + [PYREVIT\_FILE\_PREFIX\_STAMPED\_USER\_REGEX](#pyrevit.PYREVIT_FILE_PREFIX_STAMPED_USER_REGEX)
  + [ALLUSER\_PROGRAMDATA](#pyrevit.ALLUSER_PROGRAMDATA)
  + [USER\_ROAMING\_DIR](#pyrevit.USER_ROAMING_DIR)
  + [PYREVIT\_ALLUSER\_APP\_DIR](#pyrevit.PYREVIT_ALLUSER_APP_DIR)
  + [PYREVIT\_APP\_DIR](#pyrevit.PYREVIT_APP_DIR)
  + [PYREVIT\_VERSION\_APP\_DIR](#pyrevit.PYREVIT_VERSION_APP_DIR)
  + [THIRDPARTY\_EXTENSIONS\_DEFAULT\_DIR](#pyrevit.THIRDPARTY_EXTENSIONS_DEFAULT_DIR)
* [Classes](#pyrevit-classes)

  + [PyRevitException](#pyrevit.PyRevitException)

    - [Attributes](#pyrevit.PyRevitException-attributes)

      * [msg](#pyrevit.PyRevitException.msg)
  + [PyRevitIOError](#pyrevit.PyRevitIOError)

    - [Attributes](#pyrevit.PyRevitIOError-attributes)

      * [msg](#pyrevit.PyRevitIOError.msg)
  + [PyRevitCPythonNotSupported](#pyrevit.PyRevitCPythonNotSupported)

    - [Attributes](#pyrevit.PyRevitCPythonNotSupported-attributes)

      * [feature\_name](#pyrevit.PyRevitCPythonNotSupported.feature_name)
      * [msg](#pyrevit.PyRevitCPythonNotSupported.msg)
  + [\_HostApplication](#pyrevit._HostApplication)

    - [Attributes](#pyrevit._HostApplication-attributes)

      * [uiapp](#pyrevit._HostApplication.uiapp)
      * [app](#pyrevit._HostApplication.app)
      * [addin\_id](#pyrevit._HostApplication.addin_id)
      * [has\_api\_context](#pyrevit._HostApplication.has_api_context)
      * [uidoc](#pyrevit._HostApplication.uidoc)
      * [doc](#pyrevit._HostApplication.doc)
      * [active\_view](#pyrevit._HostApplication.active_view)
      * [docs](#pyrevit._HostApplication.docs)
      * [available\_servers](#pyrevit._HostApplication.available_servers)
      * [version](#pyrevit._HostApplication.version)
      * [subversion](#pyrevit._HostApplication.subversion)
      * [version\_name](#pyrevit._HostApplication.version_name)
      * [build](#pyrevit._HostApplication.build)
      * [serial\_no](#pyrevit._HostApplication.serial_no)
      * [pretty\_name](#pyrevit._HostApplication.pretty_name)
      * [is\_demo](#pyrevit._HostApplication.is_demo)
      * [language](#pyrevit._HostApplication.language)
      * [username](#pyrevit._HostApplication.username)
      * [proc](#pyrevit._HostApplication.proc)
      * [proc\_id](#pyrevit._HostApplication.proc_id)
      * [proc\_name](#pyrevit._HostApplication.proc_name)
      * [proc\_path](#pyrevit._HostApplication.proc_path)
      * [proc\_window](#pyrevit._HostApplication.proc_window)
      * [proc\_screen](#pyrevit._HostApplication.proc_screen)
      * [proc\_screen\_workarea](#pyrevit._HostApplication.proc_screen_workarea)
      * [proc\_screen\_scalefactor](#pyrevit._HostApplication.proc_screen_scalefactor)
    - [Methods:](#pyrevit._HostApplication-functions)

      * [is\_newer\_than](#pyrevit._HostApplication.is_newer_than)
      * [is\_older\_than](#pyrevit._HostApplication.is_older_than)
      * [is\_exactly](#pyrevit._HostApplication.is_exactly)
      * [get\_postable\_commands](#pyrevit._HostApplication.get_postable_commands)
      * [post\_command](#pyrevit._HostApplication.post_command)

1. [Home](../..)
2. [Reference API](_perf/)

pyrevit
=======

pyRevit root level config for all pyrevit sub-modules.

Examples:

```
from pyrevit import DB, UI
from pyrevit import PyRevitException, PyRevitIOError

# pyrevit module has global instance of the
# _HostAppPostableCommand and _ExecutorParams classes already created
# import and use them like below
from pyrevit import HOST_APP
from pyrevit import EXEC_PARAMS
```

Attributes
----------

### `PYREVIT_ADDON_NAME = 'pyRevit'` `module-attribute`

### `PYREVIT_CLI_NAME = 'pyrevit.exe'` `module-attribute`

### `VERSION_STRING = version_file.read()` `module-attribute`

### `matches = re.findall('(\\d+)\\.(\\d+)\\.(\\d+)\\.?(.+)?', VERSION_STRING)[0]` `module-attribute`

### `BUILD_METADATA = ''` `module-attribute`

### `VERSION_MAJOR = int(VERSION_MAJOR)` `module-attribute`

### `VERSION_MINOR = int(VERSION_MINOR)` `module-attribute`

### `VERSION_PATCH = int(VERSION_PATCH)` `module-attribute`

### `HOME_DIR = op.dirname(op.dirname(op.dirname(__file__)))` `module-attribute`

### `DOTNET_RUNTIME_ID = 'netcore' if compat.NETCORE else 'netfx'` `module-attribute`

### `ROOT_BIN_DIR = op.join(HOME_DIR, 'bin')` `module-attribute`

### `BIN_DIR = op.join(ROOT_BIN_DIR, DOTNET_RUNTIME_ID)` `module-attribute`

### `MAIN_LIB_DIR = op.join(HOME_DIR, 'pyrevitlib')` `module-attribute`

### `MISC_LIB_DIR = op.join(HOME_DIR, 'site-packages')` `module-attribute`

### `MODULE_DIR = op.join(MAIN_LIB_DIR, 'pyrevit')` `module-attribute`

### `LOADER_DIR = op.join(MODULE_DIR, 'loader')` `module-attribute`

### `RUNTIME_DIR = op.join(MODULE_DIR, 'runtime')` `module-attribute`

### `ADDIN_DIR = op.join(LOADER_DIR, 'addin')` `module-attribute`

### `ENGINES_DIR = op.join(BIN_DIR, 'engines', eng.EngineVersion)` `module-attribute`

### `PYREVIT_CLI_PATH = op.join(HOME_DIR, 'bin', PYREVIT_CLI_NAME)` `module-attribute`

### `TRACEBACK_TITLE = 'Traceback:'` `module-attribute`

### `HOST_APP = _HostApplication()` `module-attribute`

### `EXEC_PARAMS = _ExecutorParams()` `module-attribute`

### `DOCS = _DocsGetter()` `module-attribute`

### `USER_SYS_TEMP = os.getenv('temp')` `module-attribute`

### `USER_DESKTOP = op.expandvars('%userprofile%\\desktop')` `module-attribute`

### `EXTENSIONS_DEFAULT_DIR = op.join(HOME_DIR, 'extensions')` `module-attribute`

### `PYREVIT_FILE_PREFIX_UNIVERSAL = '{}_'.format(PYREVIT_ADDON_NAME)` `module-attribute`

### `PYREVIT_FILE_PREFIX_UNIVERSAL_REGEX = '^' + PYREVIT_ADDON_NAME + '_(?P<fname>.+)'` `module-attribute`

### `PYREVIT_FILE_PREFIX = '{}_{}_'.format(PYREVIT_ADDON_NAME, HOST_APP.version)` `module-attribute`

### `PYREVIT_FILE_PREFIX_REGEX = '^' + PYREVIT_ADDON_NAME + '_(?P<version>\\d{4})_(?P<fname>.+)'` `module-attribute`

### `PYREVIT_FILE_PREFIX_STAMPED = '{}_{}_{}_'.format(PYREVIT_ADDON_NAME, HOST_APP.version, HOST_APP.proc_id)` `module-attribute`

### `PYREVIT_FILE_PREFIX_STAMPED_REGEX = '^' + PYREVIT_ADDON_NAME + '_(?P<version>\\d{4})_(?P<pid>\\d+)_(?P<fname>.+)'` `module-attribute`

### `PYREVIT_FILE_PREFIX_UNIVERSAL_USER = '{}_{}_'.format(PYREVIT_ADDON_NAME, HOST_APP.username)` `module-attribute`

### `PYREVIT_FILE_PREFIX_UNIVERSAL_USER_REGEX = '^' + PYREVIT_ADDON_NAME + '_(?P<user>.+)_(?P<fname>.+)'` `module-attribute`

### `PYREVIT_FILE_PREFIX_USER = '{}_{}_{}_'.format(PYREVIT_ADDON_NAME, HOST_APP.version, HOST_APP.username)` `module-attribute`

### `PYREVIT_FILE_PREFIX_USER_REGEX = '^' + PYREVIT_ADDON_NAME + '_(?P<version>\\d{4})_(?P<user>.+)_(?P<fname>.+)'` `module-attribute`

### `PYREVIT_FILE_PREFIX_STAMPED_USER = '{}_{}_{}_{}_'.format(PYREVIT_ADDON_NAME, HOST_APP.version, HOST_APP.username, HOST_APP.proc_id)` `module-attribute`

### `PYREVIT_FILE_PREFIX_STAMPED_USER_REGEX = '^' + PYREVIT_ADDON_NAME + '_(?P<version>\\d{4})_(?P<user>.+)_(?P<pid>\\d+)_(?P<fname>.+)'` `module-attribute`

### `ALLUSER_PROGRAMDATA = PyRevitLabsCommon.PyRevitLabsConsts.PyRevitProgramDataPath` `module-attribute`

### `USER_ROAMING_DIR = PyRevitLabsCommon.PyRevitLabsConsts.PyRevitPath` `module-attribute`

### `PYREVIT_ALLUSER_APP_DIR = ALLUSER_PROGRAMDATA` `module-attribute`

### `PYREVIT_APP_DIR = USER_ROAMING_DIR` `module-attribute`

### `PYREVIT_VERSION_APP_DIR = op.join(PYREVIT_APP_DIR, HOST_APP.version)` `module-attribute`

### `THIRDPARTY_EXTENSIONS_DEFAULT_DIR = op.join(PYREVIT_APP_DIR, 'Extensions')` `module-attribute`

Classes
-------

### `PyRevitException`

Bases: `Exception`

Common base class for all pyRevit exceptions.

Parameters args and message are derived from Exception class.

#### Attributes

##### `msg` `property`

Return exception message.

### `PyRevitIOError`

Bases: `PyRevitException`

Common base class for all pyRevit io-related exceptions.

#### Attributes

##### `msg` `property`

Return exception message.

### `PyRevitCPythonNotSupported(feature_name)`

Bases: `PyRevitException`

Exception for features not supported under CPython.

Source code in `pyrevitlib/pyrevit/__init__.py`

|  |  |
| --- | --- |
| ``` 169 170 171 ``` | ``` def __init__(self, feature_name):     super(PyRevitCPythonNotSupported, self).__init__()     self.feature_name = feature_name ``` |

#### Attributes

##### `feature_name = feature_name` `instance-attribute`

##### `msg` `property`

Return exception message.

### `_HostApplication()`

Bases: `object`

Private Wrapper for Current Instance of Revit.

Provides version info and comparison functionality, alongside providing
info on the active screen, active document and ui-document, available
postable commands, and other functionality.

Examples:

```
hostapp = _HostApplication()
hostapp.is_newer_than(2017)
```

Source code in `pyrevitlib/pyrevit/__init__.py`

|  |  |
| --- | --- |
| ``` 213 214 ``` | ``` def __init__(self):     self._postable_cmds = [] ``` |

#### Attributes

##### `uiapp` `property`

Return UIApplication provided to the running command.

##### `app` `property`

Return Application provided to the running command.

##### `addin_id` `property`

Return active addin id.

##### `has_api_context` `property`

Determine if host application is in API context.

##### `uidoc` `property`

Return active UIDocument.

##### `doc` `property`

Return active Document.

##### `active_view` `property` `writable`

Return view that is active (UIDocument.ActiveView).

##### `docs` `property`

Return :obj:`list` of open :obj:`Document` objects.

##### `available_servers` `property`

Return :obj:`list` of available Revit server names.

##### `version` `property`

str: Return version number (e.g. '2018').

##### `subversion` `property`

str: Return subversion number (e.g. '2018.3').

##### `version_name` `property`

str: Return version name (e.g. 'Autodesk Revit 2018').

##### `build` `property`

str: Return build number (e.g. '20170927\_1515(x64)').

##### `serial_no` `property`

str: Return serial number number (e.g. '569-09704828').

##### `pretty_name` `property`

Returns the pretty name of the host.

Examples:

Autodesk Revit 2019.2 build: 20190808\_0900(x64)

Returns:

| Type | Description |
| --- | --- |
| `str` | Pretty name of the host |

##### `is_demo` `property`

bool: Determine if product is using demo license.

##### `language` `property`

str: Return language type (e.g. 'LanguageType.English\_USA').

##### `username` `property`

str: Return the username from Revit API (Application.Username).

##### `proc` `property`

System.Diagnostics.Process: Return current process object.

##### `proc_id` `property`

int: Return current process id.

##### `proc_name` `property`

str: Return current process name.

##### `proc_path` `property`

str: Return file path for the current process main module.

##### `proc_window` `property`

`intptr`: Return handle to current process window.

##### `proc_screen` `property`

`intptr`: Return handle to screen hosting current process.

##### `proc_screen_workarea` `property`

`System.Drawing.Rectangle`: Return screen working area.

##### `proc_screen_scalefactor` `property`

float: Return scaling for screen hosting current process.

#### Methods:

##### `is_newer_than(version, or_equal=False)`

bool: Return True if host app is newer than provided version.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `version` | `str or int` | version to check against. | *required* |
| `or_equal` | `bool` | Whether to include `version` in the comparison | `False` |

Source code in `pyrevitlib/pyrevit/__init__.py`

|  |  |
| --- | --- |
| ``` 385 386 387 388 389 390 391 392 393 394 395 ``` | ``` def is_newer_than(self, version, or_equal=False):     """bool: Return True if host app is newer than provided version.      Args:         version (str or int): version to check against.         or_equal (bool): Whether to include `version` in the comparison     """     if or_equal:         return int(self.version) >= int(version)     else:         return int(self.version) > int(version) ``` |

##### `is_older_than(version)`

bool: Return True if host app is older than provided version.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `version` | `str or int` | version to check against. | *required* |

Source code in `pyrevitlib/pyrevit/__init__.py`

|  |  |
| --- | --- |
| ``` 397 398 399 400 401 402 403 ``` | ``` def is_older_than(self, version):     """bool: Return True if host app is older than provided version.      Args:         version (str or int): version to check against.     """     return int(self.version) < int(version) ``` |

##### `is_exactly(version)`

bool: Return True if host app is equal to provided version.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `version` | `str or int` | version to check against. | *required* |

Source code in `pyrevitlib/pyrevit/__init__.py`

|  |  |
| --- | --- |
| ``` 405 406 407 408 409 410 411 ``` | ``` def is_exactly(self, version):     """bool: Return True if host app is equal to provided version.      Args:         version (str or int): version to check against.     """     return int(self.version) == int(version) ``` |

##### `get_postable_commands()`

Return list of postable commands.

Returns:

| Type | Description |
| --- | --- |
| `list[_HostAppPostableCommand]` | postable commands. |

Source code in `pyrevitlib/pyrevit/__init__.py`

|  |  |
| --- | --- |
| ``` 413 414 415 416 417 418 419 420 421 422 423 424 425 426 427 428 429 430 431 432 433 434 435 436 437 ``` | ``` def get_postable_commands(self):     """Return list of postable commands.      Returns:         (list[_HostAppPostableCommand]): postable commands.     """     # if list of postable commands is _not_ already created     # make the list and store in instance parameter     if not self._postable_cmds:         for pc in UI.PostableCommand.GetValues(UI.PostableCommand):             try:                 rcid = UI.RevitCommandId.LookupPostableCommandId(pc)                 self._postable_cmds.append(                     # wrap postable command info in custom namedtuple                     _HostAppPostableCommand(name=safe_strtype(pc),                                             key=rcid.Name,                                             id=rcid.Id,                                             rvtobj=rcid)                     )             except Exception:                 # if any error occured when querying postable command                 # or its info, pass silently                 pass      return self._postable_cmds ``` |

##### `post_command(command_id)`

Request Revit to run a command.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `command_id` | `str` | command identifier e.g. ID\_REVIT\_SAVE\_AS\_TEMPLATE | *required* |

Source code in `pyrevitlib/pyrevit/__init__.py`

|  |  |
| --- | --- |
| ``` 439 440 441 442 443 444 445 446 ``` | ``` def post_command(self, command_id):     """Request Revit to run a command.      Args:         command_id (str): command identifier e.g. ID_REVIT_SAVE_AS_TEMPLATE     """     command_id = UI.RevitCommandId.LookupCommandId(command_id)     self.uiapp.PostCommand(command_id) ``` |

Back to top

Made with
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/)