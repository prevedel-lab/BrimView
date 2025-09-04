# BrimView

3 possibles versions, using the same code:
1. Running in your python environnement and displayed in your browser
2. Fully in the browser
3. As an independent executable

## Internal dev notes
### `panel serve`
Go on https://github.com/prevedel-lab/BrimView-widgets and download the .whl
- `pip install brimview-widgets[processing, localfile, remote-store]` (automatically install brimfile and panel - use the .whl for the moment)
> or `pip install ".\path\to\brimview-widget.whl[processing, localfile, remote-store]"`
Now you can call `panel serve ./src/index.py` !

### Pyodide
- `pip install requests_cache`
- copy the wheels of brimfile and bls_panel_app_widgets into the pyodide folder
- run `py ./src/build_webapp.py`
- start the webserver: `py ./src/run_server.py`

### PyInstaller
- `pip install pywebview, pyinstaller`
- the `launcher.spec` should be configured to work fine
- `pyinstaller launcher.spec --noconfirm` to build .exe file
[ ] Problem with embedding the logo

```powershell
pyinstaller `
--name launcher `
--console `
--add-data "src/index.py;src/." `
--add-data "src/BrimView.png;src/." `
--hidden-import brimfile `
--hidden-import bls_panel_app_widgets `
--hidden-import pandas `
--copy-metadata pandas `
--onefile `
src/launcher.py
```