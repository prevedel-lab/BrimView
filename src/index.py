import sys
import panel as pn
import holoviews as hv
import xarray as xr  # Force import of xarray
import scipy
import numpy as np  # Force import of numpy
import brimfile as bls  # Force import of brimfile
import bls_panel_app_widgets as bls_widgets

hv.extension("bokeh")  # or 'plotly'/'matplotlib' depending on your use
pn.extension("plotly", "filedropper", "jsoneditor", "tabulator", notifications=True)

print("Hello world")
print(bls.__version__)

# Templates can't be dynamically changed, so we need to "pre-allocate"
# The things we need
# See: https://github.com/holoviz/panel/issues/7913#issuecomment-2880177999
# See: https://panel.holoviz.org/explanation/styling/templates_overview.html
sidebar = pn.layout.FlexBox()
main_tabs = pn.Tabs(
    sizing_mode="stretch_width",
)

# Adding a github icon linking to the project in the header
github_icon = pn.pane.HTML(
    '<a href="https://github.com/prevedel-lab/brimfile" target="_blank" style="text-decoration: none;">'
    '<img src="https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png" width="24" height="24" style="vertical-align: middle;">'
    "</a>",
)
header_row = pn.Row(pn.layout.HSpacer(), github_icon)

# Assembling the temaplte together
layout = pn.template.FastListTemplate(
    title="BrimView - a web-based Brillouin viewer and analyzer",
    header=[header_row],
    sidebar=[sidebar],
    logo="./src/BrimView.png", # relative path to where you call `panel serve`
    favicon="./src/BrimView.png",
    accent="#4099da",
    sidebar_footer="BrimView - A Brimfile Viewer. Checkout the source code on Github. License: XXX",
)

layout.main.append(main_tabs)


# This class needs to be defined here,
# Or else we get some Bokeh error:
# > Could not resolve type 'JSFileInput1', which could be due to
# > a widget or a custom model not being registered before first usage
class JSFileInput(pn.custom.JSComponent):

    def __init__(self, **params):
        super().__init__(**params)
        self.update_function = None  # Placeholder for the update function

    def set_update_function(self, update_function):
        """
        ```
        > self.update_function(_zarrFile)
        ```
        """
        self.update_function = update_function

    # bls_file = param.ClassSelector(class_=bls.File, default=None, precedence=-1, doc="The BLS file loaded by the FileInput widget")
    # this will never be executed because 'model' can't be posted to the worker
    def _handle_msg(self, msg):
        print(f"[python] handle msg ! {msg}")
        if msg["msg_type"] == "file_loaded":
            print("file loaded !")
            # the file was successfully loaded!
            if "bls_file" not in globals():
                raise ValueError("Something went wrong with loading the file!")
            bls_file = globals()["bls_file"]
            if self.update_function is not None:
                print("Calling update function")
                self.update_function(bls_file)

    _esm = """  
    export function render({ model }) {
    const file_input = document.createElement("input");
    file_input.type = "file";
    file_input.id = "file_input";
    //file_input.accept = ".h5,.hdf5";
    file_input.addEventListener("change", async (event) => {
            const file = event.target.files[0];
            //'model' can't be posted to the worker, so we have to find a different solution to notify the FileInput object

            // Set up a temporary message handler - We need to react to messages from the worker
            const onPyodideMessage = (event) => {
            const msg = event.data;
            console.log("[_esm] Received message from Pyodide worker:", msg);
            if (msg.type === "file_loaded") {
                model.send_msg({ msg_type: "file_loaded"});
            }
            };
            pyodideWorker.addEventListener("message", onPyodideMessage, { once: true });

            console.log("Sending 'load_file' message to Pyodide worker");
            pyodideWorker.postMessage({type: "load_file", file: file});
        });
    return file_input
    }
    """


# UI constructor (shared)
async def build_ui():
    # Needs to be defined before, because will vary depending on the backend
    analyser_placeholder = pn.layout.FlexBox()

    print("Building UI")
    if "pyodide" in sys.modules:  # We're in the Pyodide case
        # Creating the file input widget
        js_file_widget = JSFileInput()
        FileSelector = bls_widgets.BlsFileInput()
        js_file_widget.set_update_function(FileSelector.external_file_update)

        s3_file_selector = bls_widgets.S3FileSelector()
        s3_file_selector.set_update_function(
            lambda file_path: FileSelector.external_file_update(
                # In pyodide, bls.File expect the param to already be a correct zarr obj
                bls.File(file_path)
            )
        )

        file_widget = pn.layout.FlexBox(js_file_widget, s3_file_selector)

        # Creating the treatment widget
        TreamentWidget = pn.pane.Markdown(
            "This widget is not available in the Pyodide version of the app. "
        )
        analyser_placeholder.append(TreamentWidget)

    else:  # We're in `panel serve` case

        # Creating the file input widget
        from bls_panel_app_widgets import TinkerFileSelector

        file_widget = TinkerFileSelector()
        FileSelector = bls_widgets.BlsFileInput()
        file_widget.set_update_function(
            lambda file_path: FileSelector.external_file_update(
                bls.File(file_path, mode="a")
            )
        )

        # Creating the treatment widget
        from bls_panel_app_widgets import BlsDoTreatment

        TreamentWidget = BlsDoTreatment(FileSelector)
        # TreamentWidget = pn.pane.Markdown("TEST")
        analyser_placeholder.append(TreamentWidget)

    # ====
    # Populate main area

    # Brim Visualizer tab
    DataVisualizer = bls_widgets.BlsDataVisualizer(FileSelector)
    spectrum_visualizer = bls_widgets.BlsSpectrumVisualizer(DataVisualizer)
    brim_visualizer = pn.layout.Row(
        pn.layout.FlexBox(DataVisualizer, margin=10),
        pn.layout.FlexBox(spectrum_visualizer, margin=10),
        sizing_mode="stretch_width",
    )
    main_tabs.append((".brim Visualizer", brim_visualizer))

    # Metadata tab
    main_tabs.append(
        ("Metadata", bls_widgets.BlsMetadata(FileSelector))
    )  # Keeps this above the other, or else the loading time suffers a lot.

    # "Treatement" tab
    main_tabs.append(("Do spectrum treatement", analyser_placeholder))

    # ======
    # Populate the sidebar
    sidebar.append(file_widget)
    sidebar.append(FileSelector)
    sidebar.append(pn.VSpacer())

    print("Done building UI")


pn.state.onload(build_ui)
layout.servable()
