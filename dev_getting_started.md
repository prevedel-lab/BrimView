# BrimView developement

## Overview 
### Project and sub-projects
There are 4 main sub-projects to BrimView: 

- [BrimView-widgets](./BrimView-widgets/): contains the python code to create the different panel widgets to visualize and interact with the .brim files. **If you want to add/change/create widgets, start here**

- [BrimView](./src/): This is the "panel glue code" that mixes all the different widget together in a meaningfull way. It also contains some version-specific code, ie some .js to open zarr files in the WASM-version. You'll also find the version-specific build script. **If you want to change which widgets are displayed, start here**.

- [brimfile](https://github.com/prevedel-lab/brimfile): This is the Python library that allows to read and write .brim files. **If you want to create a (BrimView independent) brim-file compatible software, go there.** 

- [HDF5-BLS-treat](https://github.com/bio-brillouin/HDF5_BLS/tree/main/HDF5_BLS_treat): This is the Python library that allows to extract information from the recorded spectrum. **If you want to use the same processing algorithm as brim and brimX, go there** 

### Development cycle
As there are 3 deployment versions of BrimView, it might be a bit overwhelming to know where to start. The recommended developement cycle is: 

0. Make the changes you want to a BrimView-widget
1. Instantiate your widget inside the [index.py](./src/index.py)
2. Run `panel serve ./src/index.py --dev` 
3. Test your new widget, track and correct bugs, ... until you're happy. 
4. (Optional) Build the WASM-version of your app, serve it and check if the widget is behaving the same
5. (Optional) Build the standalone-version of your app, and check for bugs. 


## Installation
0. Clone this repository 
1. Create a new .venv enviromment 
2. Install BrimView-widgets: `pip install -e ".\BrimView-widgets\[processing, localfile, remote-store, statistics]`
3. You should be able to run `panel serve ./src/index.py --dev` now, and everything should be working

### Web-app
> Changes to the Web-app are a bit more tricky, because you not only need Python but also Javascript/WebDev experience. 

For the pyodide-app to work, we need to have:
- the .whl for BrimView-widget and Brimfile: run `py ./src/build_deps.py`
- the converted BrimView app: run `py ./src/build_webapp.py` (currently we only tested it on panel 1.8.3 and earlier versions do not work)
- put all of that into one folder
- have that folder be served by a web server: `py ./src/run_server.py`

Don't hesitate to open the Browser developer tools to look into the Javascript console and the downloaded files to understand why something might not behave as expected.

### Standalone version
0. Install 2 more dependancies `pip install pywebview, pyinstaller`
1. `pyinstaller launcher.spec --noconfirm` to build .exe file

The `launcher.spec` should be configured to work fine as is. 

Please keep in mind that Pyinstaller only creates an executable for the OS that it is running from (ie on Windows, you can only create a Windows executable).
