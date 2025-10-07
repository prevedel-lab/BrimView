import sys
from os import path as os_path
import panel as pn
import holoviews as hv
import xarray as xr  # Force import of xarray
import scipy
import numpy as np  # Force import of numpy
import tifffile  # Force import of tifffile
import brimfile as bls  # Force import of brimfile
import brimview_widgets
from brimview_widgets.logging import logger
from brimview_widgets.utils import running_from_pyodide
import HDF5_BLS_treat # Force import of HDF5_BLS_treat

__version__ = "0.2.2"

hv.extension("bokeh")  # or 'plotly'/'matplotlib' depending on your use
pn.extension("plotly", "filedropper", "jsoneditor", "tabulator", "modal", notifications=True)
pn.extension(
    raw_css=[
        """
.bk-tabs .bk-tab-pane[hidden] {
    pointer-events: none !important;
}
"""
    ]
)

# --- Usefull debug prints ---
logger.info("Starting Brimview...")
logger.info(f"BrimView {__version__}")
logger.info(f"brimfile {bls.__version__}")
logger.info(f"brimview-widgets {brimview_widgets.__version__}")


def resource_path(relative_path):
    # For PyInstaller: get temp folder path
    if hasattr(sys, "_MEIPASS"):
        return os_path.join(sys._MEIPASS, relative_path)
    return relative_path  # if not PyInstaller, return the original path

def parse_query_params(file_widget: brimview_widgets.TinkerFileSelector):
    """
    Read the query parameters of the URL and takes the appropriate actions.
    Important: this function should be called only after all the widgets are loaded
    """
    query_params = pn.state.location.query_params
    if 'S3_loc' in query_params and not running_from_pyodide:
        # TODO make it work also in Pyodide
        assert isinstance(file_widget, brimview_widgets.TinkerFileSelector)
        file_widget.input_and_load_s3_file(query_params['S3_loc'])


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
    '<a href="https://github.com/prevedel-lab/BrimView" target="_blank" style="text-decoration: none;">'
    '<img src="https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png" width="24" height="24" style="vertical-align: middle;">'
    "</a>",
)
header_row = pn.Row(pn.layout.HSpacer(), github_icon)

data_protection = pn.Row(
    pn.Card(
        pn.pane.HTML(
            "When you upload a file from your computer, it is processed <b>locally in your browser</b> and <b>never sent to any server</b>."
        ),
        hide_header=True,
        title="Data protection",
        sizing_mode="stretch_width",
        collapsible=False,
    )
)

_running_from_docker = brimview_widgets.utils.is_running_from_docker()
if _running_from_docker:
    data_protection = None

credits = pn.Row(
    pn.Card(
        pn.pane.HTML(
            "If you encounter any issue, please open a <a href='https://github.com/prevedel-lab/BrimView/issues'>GitHub issue</a>."
        ),
        pn.pane.HTML(
            f"<p><small>Developed with <a href='https://panel.holoviz.org/'>Panel</a> by Sebastian Hambura and Carlo Bevilacqua at <a href='https://www.prevedel.embl.de/'>Prevedel lab</a>.</small></p><p><small>BrimView {__version__}, brimfile {bls.__version__}, brimview-widgets {brimview_widgets.__version__} </small></p>",
        ),
        brimview_widgets.DebugReport(),
        hide_header=True,
        title="Credits",
        sizing_mode="stretch_width",
        collapsible=False,
    )
)

# Assembling the template together
layout = pn.template.FastListTemplate(
    title="BrimView - a web-based Brillouin viewer and analyzer",
    header=[header_row],
    sidebar=[
        sidebar,
        pn.Spacer(height=30),
        data_protection,
        pn.Spacer(height=15),
        credits,
    ],
    logo=resource_path(
        "./src/BrimView.png"
    ),  # relative path to where you call `panel serve`
    favicon=resource_path("./src/BrimView.png"),
    accent="#4099da",
)

layout.main.append(main_tabs)

if running_from_pyodide:
    # This apparently needs to be loaded here to work nicely
    custom_file_loader = brimview_widgets.CustomJSFileInput()
    sidebar.append(custom_file_loader)


# UI constructor (shared)
def build_ui():
    # Needs to be defined before, because will vary depending on the backend
    analyser_placeholder = pn.layout.FlexBox()
    TreamentWidget_webapp = pn.pane.Markdown(
            # TODO: add link to download page
            "This widget is not available in the Webapp.\nPlease download the desktop version of BrimView from [here](https://github.com/prevedel-lab/BrimView/releases/latest)"
        )

    logger.info("Building UI")
    if running_from_pyodide:  # We're in the Pyodide case
        # Creating the file input widget

        FileSelector = brimview_widgets.BlsFileInput()
        custom_file_loader.set_update_function(FileSelector.external_file_update)

        s3_file_selector = brimview_widgets.S3FileSelector()

        def load_s3_file(file_path):
            """
            This funciton is used to load a zarr file from S3 in pyodide.
            It uses the `loadZarrFile` function defined in `index.js`
            """
            from js import loadZarrFile

            if not loadZarrFile(file_path):
                raise ValueError(f"Failed to load file {file_path} from S3")
                        
            # loadZarrFile stores the bls_file in the global scope of the CustomJSFileInput module
            bls_file = brimview_widgets.CustomJSFileInput().get_global_bls()
            return bls_file

        s3_file_selector.set_update_function(
            lambda file_path: FileSelector.external_file_update(
                # In pyodide, bls.File expect the param to already be a correct zarr obj
                load_s3_file(file_path)
            )
        )

        sampledata_selector = brimview_widgets.SampledataLoader()
        sampledata_selector.set_update_function(
            lambda file_path: FileSelector.external_file_update(
                # In pyodide, bls.File expect the param to already be a correct zarr obj
                load_s3_file(file_path)
            )
        )

        file_widget = pn.layout.FlexBox(
            pn.Card(s3_file_selector, title="S3 online data", margin=5),
        )

        # Creating the treatment widget
        TreamentWidget = TreamentWidget_webapp

    else:  # We're in `panel serve` case

        # Creating the file input widget
        file_widget = brimview_widgets.TinkerFileSelector()
        FileSelector = brimview_widgets.BlsFileInput()
        file_widget.set_update_function(
            lambda file_path: FileSelector.external_file_update(
                bls.File(file_path, mode="a")
            )
        )
        sampledata_selector = brimview_widgets.SampledataLoader()
        sampledata_selector.set_update_function(
            lambda file_path: FileSelector.external_file_update(
                bls.File(file_path, mode="a")
            )
        )

        # Creating the treatment widget
        if _running_from_docker: 
            TreamentWidget = TreamentWidget_webapp
        else:
            TreamentWidget = brimview_widgets.BlsDoTreatment(FileSelector)
        
    
    analyser_placeholder.append(TreamentWidget)

    # ====
    # Populate main area

    # Brim Visualizer tab
    DataVisualizer = brimview_widgets.BlsDataVisualizer(FileSelector)
    spectrum_visualizer = brimview_widgets.BlsSpectrumVisualizer(DataVisualizer)
    brim_visualizer = pn.layout.Row(
        pn.layout.FlexBox(DataVisualizer, margin=10),
        pn.layout.FlexBox(spectrum_visualizer, margin=10),
        sizing_mode="stretch_width",
    )
    main_tabs.append((".brim Visualizer", brim_visualizer))

    # Metadata tab
    bls_metadata_widget = brimview_widgets.BlsMetadata(value=FileSelector.param.data)
    main_tabs.append(("Metadata", bls_metadata_widget))

    # === UI Bug workaround ===
    # Without this, when the tabulator gets data, it comes to the top of the DOM,
    # even if it's in the non-active tab.
    # This workaround works in both pyodide and panel serve
    #
    # Putting tabs to dynamic doesn't work in pyodide-converted
    # Potentially related to:
    # - https://github.com/holoviz/panel/issues/8053
    # - https://github.com/holoviz/panel/issues/8103
    bls_metadata_widget.tabulator_visibility = False  # Hidden by default

    def control_metadata_widget(active_tab_index):
        bls_metadata_widget.tabulator_visibility = (
            active_tab_index == 1
        )  # Make sure this magic number corresponds to the metadata tab index
        logger.debug(f"Metadata tab active: {bls_metadata_widget.tabulator_visibility}")

    pn.bind(control_metadata_widget, main_tabs.param.active, watch=True)
    # ======

    # "Treatement" tab
    main_tabs.append(("(Re-)analyze spectra", analyser_placeholder))

    # ======
    # Populate the sidebar
    sidebar.append(file_widget)
    sidebar.append(sampledata_selector)
    sidebar.append(FileSelector)
    sidebar.append(pn.VSpacer())

    parse_query_params(file_widget)

    logger.info("Done building UI")

build_ui()
layout.servable()
