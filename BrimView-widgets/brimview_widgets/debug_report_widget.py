import panel as pn

import brimfile as bls
import HDF5_BLS_treat.treat as bls_processing
import brimview_widgets
import sys
from importlib.metadata import version, packages_distributions

from urllib.parse import urljoin

def get_client_info():
    """
    Returns a dict with full URL, browser type/version, OS, device, and language.
    Works only in a running Panel server context.
    """
    ctx = pn.state.curdoc.session_context
    if ctx and ctx.request:
        # Full URL
        scheme = ctx.request.headers.get("X-Forwarded-Proto", "http")
        host = ctx.request.host  # includes port
        full_url = f"{scheme}://{host}{ctx.request.uri}"

        # Browser info
        user_agent = ctx.request.headers.get("User-Agent", "Unknown")

        # Language
        language = ctx.request.headers.get("Accept-Language", "Unknown")

        return {
            "url": full_url,
            "user_agent": user_agent,
            "language": language
        }

    return {
        "url": None,
        "user_agent": None,
        "language": None
    }

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
    results = {}
    dists_map = packages_distributions()  # maps top-level modules to distributions

    for name, module in sys.modules.items():
        if module is None:
            continue

        top_level = name.split(".")[0]
        if top_level not in dists_map:
            # not a third-party distribution (probably stdlib or builtin)
            continue

        for dist in dists_map[top_level]:
            try:
                results[top_level] = version(dist)
            except Exception:
                results[top_level] = None

    return results

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
        
        client_info = "\n".join(f"{k}: {v}" for k, v in get_client_info().items())
        # The markdown string needs to be without tabs to be properly displayed in the widget. 
        return pn.pane.Markdown(
            f"""
## General environment information:  

```
{client_info}
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
