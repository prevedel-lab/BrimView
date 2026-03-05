import panel as pn
import param
from enum import Enum
from panel.widgets.base import WidgetBase
from .utils import catch_and_notify
from .environment import running_from_pyodide
from .logging import logger

class JsPyMessage(str, Enum):
    """
    The different message that can be passed from the frontend (GUI, js) to the backend (panel, Python)
    for JSFileInput
    """

    TYPE = "msg_type"
    DUMMY = "dummy"
    FILE_LOADED = "file_loaded"
    ERROR = "error"
    ERROR_DETAILS = "error_details"


class CustomJSFileInput(WidgetBase):
    """
    This widget is a (panel) button, that triggers an html file_input, that forwards some information 
    to the pyodideWorker to asynchroniously read and load some zarr file (from javascript). 

    There's quite a bit of Bokeh, pytohn and javascript hacking. 

    For some reason, this widget doesn't work if it's create by pn.state.onload and into a sidebar.
    """

    # The file_input callback code that is used if panel.state._is_pyodide
    # This has to play nicely with the custom javascript / zarr / brimfile code from Carlo
    _pyodide_fileinput_callback = f"""
        console.log("[js] file or folder input callback");
        const fileList = event.target.files;
        if(fileList.length === 0 ) return;
        const files = Array.from(fileList);
    
        //'model' can't be posted to the worker, so we have to find a different solution to notify the FileInput object

        // Set up a temporary message handler - We need to react to messages from the worker
        const onPyodideMessage = (event) => {{
            const msg = event.data;
            console.log("[_esm] Received message from Pyodide worker:", msg);

            // Constructing the message to be sent to python / the backend
            let msg_to_py;
            if (msg.type === "file_loaded" || msg.type === "folder_loaded") {{
                msg_to_py = ({{ {JsPyMessage.TYPE.value}: "{JsPyMessage.FILE_LOADED.value}" }});
            }} else {{
                msg_to_py = ({{ {JsPyMessage.TYPE.value}: "{JsPyMessage.ERROR.value}", {JsPyMessage.ERROR_DETAILS.value}: msg.type }});
            }}

            py_item.value = "" ;  // reset the value so that the next message will be detected
            py_item.change.emit();
            py_item.value = msg_to_py ;
            py_item.change.emit();
            console.log("[js] sent a msg to python: ", msg_to_py);
            }};
        pyodideWorker.addEventListener("message", onPyodideMessage, {{ once: true }});

        if(files.length === 1 && files[0].name.endsWith('.zip')) {{
            console.log("Sending zip file");
            pyodideWorker.postMessage({{type: "load_file", file: files[0]}});
        }} else {{
            console.log("Sending folder structure to Pyodide worker");
            pyodideWorker.postMessage({{type: "load_file", file: files}});
        }}
     


    """

    # the file_input callback that is used if we're running in `panel serve`
    # Basically does nothing interesting, but usefull for testing/debugging reasons
    _fileinput_callback = f"""
        console.log("[js] Fileinput callback");

        // Sending back a mock message
        let msg_to_py = ({{ {JsPyMessage.TYPE.value}: "{JsPyMessage.DUMMY.value}"}});
        //let msg_to_py = ({{ {JsPyMessage.TYPE.value}: "{JsPyMessage.ERROR.value}", {JsPyMessage.ERROR_DETAILS.value}: "blabla"}});
        
        py_item.value = "" ;  // reset the value so that the next message will be detected
        py_item.change.emit();
        py_item.value = msg_to_py ;
        py_item.change.emit();
        console.log("[js] sent a msg to python: ", msg_to_py);
    """

    value = param.Parameter(
        default=None,
        doc="""This variable will be used to communicate between JS and Python. It's expected to be JSON""",
    )

    def __init__(self, **params):
        super().__init__(**params)
        self.update_function = None  # Placeholder for the update function 
        # Invisible HTML file input button
        self._html_file_button_id = "fileElem"
        self._html_folder_button_id = "folderElem"
        self._html_button = pn.pane.HTML(
            f"""
            <input type="file" id="{self._html_folder_button_id}" webkitdirectory directory multiple/>
            <input type="file" id="{self._html_file_button_id}" accept=".zip"/>
            """
        )
        self._html_button.visible = False

        # Standard panel button
        # Clicking on it triggers file_input.click()
        self._panel_button_zip = pn.widgets.Button(name="Load a .zip file", button_type="primary", width=200)
        self._panel_button_zarr = pn.widgets.Button(name="Load a .zarr folder", button_type="primary", width=200)
        self.apply_jscallback()

    def apply_jscallback(self):
        logger.info("Updating callback of the panel button")
        def js_code_onclick(button_id):
            return f"""
             let file_input = Bokeh.index.query_one((view) => view.model.id == html_file_input.id).el.shadowRoot.getElementById("{button_id}");
             file_input.addEventListener("change", async (event) => {{
                 { self._pyodide_fileinput_callback if running_from_pyodide else self._fileinput_callback }
             }}, 
             {{ once: true }});

            file_input.click() ;
            console.log("Forwarding click to the proper file_input html button")
            """
        self._panel_button_zip.jscallback(
            clicks=js_code_onclick(self._html_file_button_id),
            args={"html_file_input": self._html_button, "py_item": self},
        )
        self._panel_button_zarr.jscallback(
            clicks=js_code_onclick(self._html_folder_button_id),
            args={"html_file_input": self._html_button, "py_item": self},
        )

    @classmethod
    @catch_and_notify(prefix="<b>[CustomJSFileinput.set_global_bls]:</b>")
    def set_global_bls(cls, value):
        """
        Set a global variable `bls_file` to the given value.
        This makes sure `bls_file` can then be accesed form self._process_js_msg
        
        Expected to be called from pyodide / js.
        """
        global bls_file
        bls_file = value
        logger.debug("Set global bls_file")

    @classmethod
    @catch_and_notify(prefix="<b>[CustomJSFileinput.get_global_bls]:</b>")
    def get_global_bls(cls):
        """
        Returns the global variable `bls_file` ("global" for this module).
        
        You sohuld only have to call this function if you're doing 
        non-trivial js <-> python communication outside of the panel framework.
        """

        if "bls_file" not in globals():
            raise ValueError("Something went wrong with loading the file!")
        bls_file = globals()["bls_file"]
        logger.debug("Got global bls_file")
        return bls_file


    @pn.depends("value", watch=True)
    @catch_and_notify(prefix="<b>[CustomJSFileinput._process_js_msg]:</b>")
    def _process_js_msg(self):
        if self.value == "":
            logger.info("No message to process")
            return
        
        msg = self.value
        self.value = ""  # resets the value so JS can send a new message

        logger.debug(f"[python] handle msg ! {msg}")
        if msg[JsPyMessage.TYPE.value] == JsPyMessage.FILE_LOADED.value:
            logger.debug("file loaded !")
            # the file was successfully loaded!
            if "bls_file" not in globals():
                logger.debug(globals().keys())
                raise ValueError("Something went wrong with loading the file!")
            bls_file = globals()["bls_file"]
            if self.update_function is not None:
                logger.debug("Calling update function")
                self.update_function(bls_file)

        elif msg[JsPyMessage.TYPE.value] == JsPyMessage.DUMMY.value:
            logger.debug("Received a mock message from js/frontend")

        elif msg[JsPyMessage.TYPE.value] == JsPyMessage.ERROR.value:
            error_details = (
                msg[JsPyMessage.ERROR_DETAILS.value]
                if msg[JsPyMessage.ERROR_DETAILS.value]
                else "Unknown error while loading the file"
            )
            raise Exception(error_details)

    def set_update_function(self, update_function):
        """
        ```
        > self.update_function(_zarrFile)
        ```
        """
        self.update_function = update_function

    def __panel__(self):
        return pn.Card(
            pn.pane.HTML('See <a href="https://prevedel-lab.github.io/brimfile/brimfile.html#store-types" target="_blank" rel="noopener noreferrer">documentation</a> for supported formats.'), 
            pn.FlexBox(self._html_button, self._panel_button_zip, self._panel_button_zarr),
            title="Local data",
            margin=5,
        )
