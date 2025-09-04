import panel as pn
import param
import pandas as pd
import brimfile as bls

import tempfile
import os

from panel.io import hold

from panel.widgets.base import WidgetBase
from panel.custom import PyComponent

from .utils import catch_and_notify
from .widgets import HorizontalEditableIntSlider

class BlsFileInput(WidgetBase, PyComponent):
    """
    Class to read HDF5 files and select data groups.
    """

    # this isn't a proper FileSelector, because we use a FileDropper widget
    local_file = param.FileSelector(precedence=-1)
    debug = param.Boolean(default=False, label="Debug Mode")
    write_allowed = param.Boolean(default=False, label="Allowed to write to file")

    data_group = param.Selector(default=None, objects=[], label="Select data group")
    data_group_index = param.Integer(default=0, label="Data group index", precedence=-1)

    data_parameter = param.Selector(default=None, label="Select parameter")

    # Invisible (from the GUI) field
    bls_file = param.ClassSelector(
        class_=bls.File, default=None, precedence=-1, allow_refs=True
    )
    data = param.ClassSelector(class_=bls.Data, default=None, precedence=-1)

    def __init__(self, **params):
        params["name"] = "File input"
        super().__init__(**params)

        self.spinner = pn.indicators.LoadingSpinner(
            value=False, size=20, name="Idle", visible=True
        )

        self.datagroup_selector_widget = pn.widgets.Select.from_param(
            self.param.data_group, name="Data Group", disabled=True
        )
        self.data_group_index_widget = HorizontalEditableIntSlider.from_param(
            self.param.data_group_index, name="Index", disabled=True
        )
        self.data_group_index_widget.tooltip_text="Change which data group is displayed"
        self.data_group_index_widget.tooltip_range_or_fixed_range = True 

        def _link_index_to_group(event):
            if self.data_group_index is not None and self.data_group is not None:
                self.data_group = list(self.param.data_group.objects.values())[
                    self.data_group_index
                ]
        def _link_group_to_index(event):
            if self.data_group is not None and self.data_group_index is not None:
                index = list(self.param.data_group.objects.values()).index(self.data_group)
                self.data_group_index = index
        pn.bind(_link_index_to_group, self.param.data_group_index, watch=True)
        pn.bind(_link_group_to_index, self.param.data_group, watch=True)

        self.parameter_selector_widget = pn.widgets.Select.from_param(
            self.param.data_parameter, name="Parameter", visible=False
        )
    

    @pn.depends("loading", watch=True)
    def loading_spinner(self):
        """
        Controls an additional spinner UI.
        This goes on top of the `loading` param that comes with panel widgets.

        This is especially usefull in the `panel convert` case,
        because some UI elements can't updated easily (or at least in the same way as `panel serve`).
        In particular, the visible toggle is not always working, and elements inside Rows and Columns sometimes
        don't get updated.
        """
        with param.parameterized.batch_call_watchers(self.spinner):
            if self.loading:
                print("Setting loading spinner to true")
                self.spinner.value = True
                self.spinner.name = "Loading..."
                self.spinner.visible = True
            else:
                print("Setting loading spinner to false")
                self.spinner.value = False
                self.spinner.name = "Idle"
                self.spinner.visible = True

    @pn.depends("bls_file", watch=True)
    def _update_header(self):
        # This might be a bit Panel anti-pattern, but it seems to be 
        # the only way to make it also work as expected in the `panel convert` case
        # If you returned the header/FlexBox directly, then the spinner wouldn't update later on
        if self.bls_file is None:
            title = self.name
        else:
            title = self.bls_file.filename 
             
        self._header = pn.FlexBox(
            pn.pane.Markdown(f"### {title}"), 
            self.spinner, 
            align_content = "space-between", 
            align_items="center",  # Vertical-ish
            sizing_mode='stretch_width',
            justify_content = "space-between"
        )

    @catch_and_notify(prefix="<b>Loading file: </b>")
    def external_file_update(self, file: bls.File):
        """
        Create a new BLS file object from an existing file.
        This is used to create a new BLS file object from a file that has been uploaded.
        """
        try:
            self.loading = True
            if self.bls_file is not None:
                # Manual reset
                self.data_group = None
                self.data_parameter = None
                self.bls_file = None

            self.bls_file = file
        except Exception as e:
            # Re-throwing the exception to be caught by the decorator
            raise e
        finally:
            # Making sure the spinner is turned off
            self.loading = False

        print(f"New BLS file created: {self.bls_file}")

    @param.depends("local_file", watch=True)
    @catch_and_notify(prefix="<b>Open file: </b>")
    def _process_fileDropper(self):
        if self.local_file is None:
            return

        # print(self.local_file)
        for key, value in self.local_file.items():
            print(key)
            filename = key
            file_bytes = value
        # Create the full path in the system's temp directory
        tmp_dir = tempfile.gettempdir()
        file_path = os.path.join(tmp_dir, filename)
        print(file_path)
        # Save the uploaded file content to the temporary file
        with open(file_path, "wb") as f:
            f.write(file_bytes)

        # Now load the file using your custom BLS loader
        self.bls_file = bls.File(file_path, mode=self._file_open_mode())
        print(f"Loaded file: {file_path}")

    def _file_open_mode(self):
        if self.write_allowed:
            mode = "a"
        else:
            mode = "r"
        return mode

    @param.depends("debug", watch=True)
    @catch_and_notify(prefix="<b>Open file: </b>")
    def _load_file(self):
        print("Loading file")
        if self.debug:
            print("Debug mode is on")
            # Load the example HDF5 file
            self.bls_file = bls.File(
                "./bls_examples/test.bls.zip", mode=self._file_open_mode()
            )
        else:
            if self.local_file is None:
                print("No file loaded")
                self.bls_file = None
                return

            filename = None
            for key, value in self.local_file.items():
                filename = key
                # file_bytes = value

            if filename is None:
                print("No file is currently loaded")
                self.bls_file = None
                return

            tmp_dir = tempfile.gettempdir()
            file_path = os.path.join(tmp_dir, filename)
            self.bls_file = bls.File(file_path, mode=self._file_open_mode())

    @param.depends("bls_file", watch=True)
    def _parse_file(self):
        if self.bls_file is None:
            self.datagroup_selector_widget.disabled = True
            self.data_group_index_widget.disabled = True
            self.param.data_group.objects = {}
            self.data_group = None

        else:
            print("Parsing bls_file")
            self.datagroup_selector_widget.disabled = False
            self.data_group_index_widget.disabled = False

            # Making sure the returned list is sorted by index
            cleaned_data_group_list = {}
            data_groups = self.bls_file.list_data_groups()
            data_groups = sorted(data_groups, key=lambda x: x["index"])
            for data in data_groups:
                cleaned_data_group_list[data["name"]] = data["index"]

            # Using newly retrieved data groups
            self.param.data_group.objects = cleaned_data_group_list
            self.data_group_index_widget.end = len(cleaned_data_group_list) - 1
            self.data_group_index_widget.start = 0

            print(f"Data groups: {cleaned_data_group_list.values()}")
            self.data_group = list(cleaned_data_group_list.values())[0]

            if len(cleaned_data_group_list) == 1:  # small GUI bonus
                self.datagroup_selector_widget.disabled = True
                self.data_group_index_widget.disabled = True

    @param.depends("data_group", watch=True)
    @catch_and_notify(prefix="<b>Update data: </b>")
    def _update_data(self):
        print("_update_data")
        if self.bls_file is not None and self.data_group is not None:
            self.data = self.bls_file.get_data(self.data_group)
            print("Data loaded:", self.data.get_name())
        else:
            self.data = None

    @param.depends("data", watch=True)
    @catch_and_notify(prefix="<b>Update parameters: </b>")
    def _update_parameters(self):
        if self.data is not None:
            (parameters, names) = self.data.get_parameters()
            print(parameters)
            self.param.data_parameter.objects = {"Placeholder": "Parameter 1"}
            # TODO: make this cleaner once the get_parmeters is working
            if parameters is not None:
                self.param.data_parameter.objects = parameters
                self.data_parameter = parameters[0]
                self.parameter_selector_widget.visible = True
            else:
                self.param.data_parameter.objects = {}
                self.data_parameter = None
                # self.parameter_selector_widget.disabled = True
                self.parameter_selector_widget.visible = False

    def get_bh5_file(self):
        return self.bls_file

    @hold()
    @catch_and_notify(prefix="<b>Reload fle: </b>")
    def reload_file(self):
        """Reload the BLS file, keeping the current data group and parameters.
        This is useful if the you're writing some new content to the file, and
        you want to make sure the writing buffer is flushed and the file is reloaded.

        (for example, in case of .zip storage, makes sure the data index file is updated)
        """
        if self.bls_file is not None:
            # Keep info about what's currently loaded
            filepath = self.bls_file.filename
            data_index = self.data.get_index()
            if self.bls_file.is_read_only():
                mode = "r"
            else:
                mode = "a"

            self.bls_file.close()  # Close the file to save changes
            self.bls_file = bls.File(filepath, mode=mode)  # Reopen in append mode
            self.data = self.bls_file.get_data(data_index)

    def __panel__(self):
        if pn.state._is_pyodide:
            rw_toggle = None 
        else:
            rw_toggle = pn.widgets.Toggle.from_param(
                self.param.write_allowed,
                icon="pencil",
                name="Open with Write Access",
                button_type="warning",
                button_style="outline",
            )

        self._update_header()    
        return pn.Column(
            self._header,
            rw_toggle,
            self.datagroup_selector_widget,
            self.data_group_index_widget,
            self.parameter_selector_widget,
        )
