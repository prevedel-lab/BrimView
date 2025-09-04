import panel as pn
import param
from enum import Enum
from panel.widgets.base import WidgetBase

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
    This is just a small panel widget that forwards a click on a panel button to a JSFileInput
    #TODO
    """

    _pyodide_fileinput_callback = f"""
        console.log("[js] Fileinput callback");
        const file = event.target.files[0];
        //'model' can't be posted to the worker, so we have to find a different solution to notify the FileInput object

        // Set up a temporary message handler - We need to react to messages from the worker
        const onPyodideMessage = (event) => {{
            const msg = event.data;
            console.log("[_esm] Received message from Pyodide worker:", msg);
            if (msg.type === "file_loaded") {{
                let msg = ({{ {JsPyMessage.TYPE.value}: "{JsPyMessage.FILE_LOADED.value}" }});
            }} else {{
                let msg = ({{ {JsPyMessage.TYPE.value}: "{JsPyMessage.ERROR.value}", {JsPyMessage.ERROR_DETAILS.value}: msg.type }});
            }}

            py_item.value = msg ;
            py_item.change.emit();
            console.log("[js] sent a msg to python: ", msg);
            }};
        pyodideWorker.addEventListener("message", onPyodideMessage, {{ once: true }});

        console.log("Sending 'load_file' message to Pyodide worker");
        pyodideWorker.postMessage({{type: "load_file", file: file}});
    """

    _fileinput_callback = f"""
        console.log("[js] Fileinput callback");

        // Sending back a mock message
        let msg = ({{ {JsPyMessage.TYPE.value}: "{JsPyMessage.DUMMY.value}"}});
        //let msg = ({{ {JsPyMessage.TYPE.value}: "{JsPyMessage.ERROR.value}", {JsPyMessage.ERROR_DETAILS.value}: "blabla"}});
        py_item.value = msg ;
        py_item.change.emit();
         console.log("[js] sent a msg to python: ", msg);
    """

    value = param.Parameter(
        default=None,
        doc="""This variable will be used to communicate between JS and Python. It's expected to be JSON""")

    def __init__(self, **params):
        super().__init__(**params)
        self.update_function = None  # Placeholder for the update function

        # Invisible HTML file input button
        self._html_button_id = "fileElem"
        self._html_button = pn.pane.HTML(
            f"""<input type="file" id="{self._html_button_id}" multiple accept="image/*" />"""
        )
        self._html_button.visible = True

        # Standard panel button
        # Clicking on it triggers file_input.click()
        self._panel_button = pn.widgets.Button(name="Load a file")
        self.apply_jscallback()

    def apply_jscallback(self):
        print("Updating callback of the panel button")
        self._panel_button.jscallback(
            clicks=f"""
            console.log("test") ;
             let file_input = Bokeh.index.query_one((view) => view.model.id == html_file_input.id).el.shadowRoot.getElementById("{self._html_button_id}");
             console.log(file_input) ;
             file_input.click() ;
             console.log("Forwarding click to the proper file_input html button")
 
             file_input.addEventListener("change", async (event) => {{
                 { self._fileinput_callback if pn.state._is_pyodide else self._fileinput_callback }
 
             }});


            """,
            args={"html_file_input": self._html_button, "py_item": self},
        )


    @pn.depends("value", watch=True)
    def _process_js_msg(self):
        msg = self.value
        print(f"[python] handle msg ! {msg}")
        if msg[JsPyMessage.TYPE.value] == JsPyMessage.FILE_LOADED.value:
            print("file loaded !")
            # the file was successfully loaded!
            if "bls_file" not in globals():
                raise ValueError("Something went wrong with loading the file!")
            bls_file = globals()["bls_file"]
            if self.update_function is not None:
                print("Calling update function")
                self.update_function(bls_file)

        elif msg[JsPyMessage.TYPE.value] == JsPyMessage.DUMMY.value:
            print("Received a mock message from js/frontend")

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
        return pn.FlexBox(self._html_button, self._panel_button)
