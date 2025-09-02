import panel as pn
import param
import tkinter as tk

from tkinter import filedialog
from tkinterdnd2 import TkinterDnD, DND_FILES  # Requires tkinterdnd2 library

from .s3file_selector import S3FileSelector

def load_file_dialog() -> str | None:
    file_path_out = None
    root = tk.Tk()
    root.withdraw()  # Hide the root window
    root.attributes("-topmost", True)
    file_path_out = filedialog.askopenfilename(title="Select a file")
    if file_path_out == "":
        file_path_out = None
    root.destroy()
    return file_path_out


def drag_and_drop_dialog() -> str | None:
    file_path_out = None

    def on_drop(event):
        nonlocal file_path_out
        file_path_out = event.data
        if file_path_out:
            file_path_out = file_path_out.strip(
                "{}"
            )  # Remove curly braces if present (if spaces in file name)
        nonlocal root
        root.destroy()

    # Create the main application window
    root = TkinterDnD.Tk()  # Use TkinterDnD for drag-and-drop functionality
    root.title("Drag and Drop File")
    root.attributes("-topmost", True)
    # root.state('zoomed')

    # Create a label to display instructions
    label = tk.Label(
        root,
        text="Drag and drop a file here",
        bg="lightgray",
        relief="ridge",
        width=40,
        height=10,
    )
    label.pack(pady=20, padx=20)

    # Bind the drop event to the label
    label.drop_target_register(DND_FILES)
    label.dnd_bind("<<Drop>>", on_drop)

    # Run the application
    root.mainloop()
    return file_path_out


class TinkerFileSelector(pn.viewable.Viewer):
    """
    Custom FileSelector that uses a FileDropper widget to select files.

    This only works in a local environment, as it uses tkinter for file dialogs,
    ie. by running the app with `panel serve`.
    """

    def __init__(self, **params):
        super().__init__(**params)
        self.local_file = None

        # Filedialog button
        self.filedialog_button = pn.widgets.Button(
            name="Click me to select a file", button_type="primary", width=200
        )
        self.filedialog_button.on_click(self._select_file_dialog)

        self.dragNdrop_button = pn.widgets.Button(
            name="Click me to drag and drop a file", button_type="primary", width=200
        )
        self.dragNdrop_button.on_click(self._drag_and_drop_dialog)

        # S3 link input
        self.s3FileSelector = S3FileSelector()

    def _load_s3_file(self, event):
        s3_path = self.s3_link.value
        if s3_path:
            print(f"Selected file: {s3_path}")
            self._after_path_select(s3_path)
        else:
            print("No file selected.")


    def _select_file_dialog(self, event):
        """
        Opens a file dialog to select a file.
        """
        file_path = load_file_dialog()
        if file_path:
            print(f"Selected file: {file_path}")
            self._after_path_select(file_path)
        else:
            print("No file selected.")

    def _drag_and_drop_dialog(self, event):
        """
        Opens a drag-and-drop dialog to select a file.
        """
        file_path = drag_and_drop_dialog()
        if file_path:
            print(f"Selected file: {file_path}")
            self._after_path_select(file_path)
        else:
            print("No file selected.")

    def _after_path_select(self, file_path: str):
        if self.process_path_fn is not None:
            self.process_path_fn(file_path)

    def set_update_function(self, func):
        """
        Set the function to be called when a file is selected.
        This function should accept a single argument, which is the path to the selected file.
        """
        self.process_path_fn = func
        self.s3FileSelector.set_update_function(func)

    def __panel__(self):
        return pn.FlexBox(
            pn.Card(
                self.filedialog_button,
                self.dragNdrop_button,
                title="Local data",
                margin=5

            ), 
            pn.Card(
                self.s3FileSelector,
                title="S3 online data",
                margin=5
            )
        )
