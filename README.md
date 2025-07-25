# BrimView

3 possibles versions, using the same code:
1. Running in your python environnement and displayed in your browser
2. Fully in the browser
3. As an independent executable

## Internal dev notes
### `panel serve`
- `pip install panel`
- `pip install ../brimfile`
- `pip install ../bls-app-panel` (this needs to become it's own proper package - bls_panel_app_widgets)
Also (manual, but should come as dependencies from bls-app-panel)
- `pip install holoviews, xarray, scipy, numpy`
- `pip install tkinterdnd2`
- `pip install HDF5_BLS_treat`
Now you can call `panel serve ./src/index.py` !

### Pyodide
- `pip install requests_cache`
- copy the wheels of brimfile and bls_panel_app_widgets into the pyodide folder
- start the webserver: `py ./src/run_server.py`

### PyInstaller
- `pip install pywebview, pyinstaller`
- the `launcher.spec` should be configured to work fine
- `pyinstaller launcher.spec --noconfirm` to build .exe file
[ ] Problem with embedding the logo