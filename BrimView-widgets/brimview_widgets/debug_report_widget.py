import panel as pn

import brimfile as bls
import HDF5_BLS_treat.treat as bls_processing
import brimview_widgets
import sys
import importlib.metadata

from urllib.parse import urljoin

def get_url():
    """
    Returns a dict with full URL, browser type/version, OS, device, and language.
    Works only in a running Panel server context.
    """
    return f"url: {pn.state.location.href}"

def browser_info():
    ctx = pn.state.curdoc.session_context
    if ctx and ctx.request:
        ua = ctx.request.headers.get("User-Agent", "Unknown")
        lang = ctx.request.headers.get("Accept-Language", "Unknown")
        return f"User-Agent: {ua}\nLanguage: {lang}"
    return "No browser info (probably not in a server context)"

def python_version() -> str:
    return f"Python {sys.version.split()[0]} on {sys.platform}"

def bls_versions() -> str:
    s = ""
    s += f"is_pyodide: {pn.state._is_pyodide}\n"
    s += f"brimfile: {bls.__version__}\n"
    s += f"brimview_widgets: {brimview_widgets.__version__}\n"
    s += f"HDF5_BLS_treat: {bls_processing.Treat_version}"
    return s

def get_loaded_third_party_versions():
    """
    Return a dict {module_name: version} for all currently loaded third-party modules.
    Standard library and built-ins are ignored.
    """

    # Map top-level module to its distribution version
    module_to_version = {}
    loaded_modules = set(name.split('.')[0] for name in sys.modules if sys.modules[name] is not None)

    for dist in importlib.metadata.distributions():
        try:
            top_levels = dist.read_text('top_level.txt')
            if not top_levels:
                continue
            for top_level in top_levels.splitlines():
                if top_level in loaded_modules:
                    module_to_version[top_level] = dist.version
        except Exception:
            continue

    return module_to_version

class DebugReport(pn.viewable.Viewer):

    def __init__(self, **params):
        super().__init__(**params)
        # self._debug_button = pn.widgets.ButtonIcon(icon="bug", description="Display debug report")
        self._debug_report = pn.Modal(pn.Column(self._env_report(), scroll=True, height=400))
        self._debug_button = self._debug_report.create_button(
            "toggle",
            name="Display debug report",
            icon="bug",
            button_style="outline",
            icon_size="1.1em",
        )
    
    def _env_report(self):
        other_libs = get_loaded_third_party_versions()  
        other_libs_v = ""  
        for mod, ver in other_libs.items():
            other_libs_v += f"{mod}: {ver} \n"
        
        # The markdown string needs to be without tabs to be properly displayed in the widget. 
        return pn.pane.Markdown(
            f"""
## General environment information:  

```
{get_url()}
{browser_info()}
{python_version()}  
```

## Brillouin libraries: 

```
{bls_versions()}
```

## 3rd party libraries:  

```
{other_libs_v}
```
            """

        )



    def __panel__(self):
        return pn.FlexBox(self._debug_button, self._debug_report, margin=5, align='center')
