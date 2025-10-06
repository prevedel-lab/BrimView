import panel as pn

from .utils import catch_and_notify
from .logging import logger

class S3FileSelector(pn.viewable.Viewer):

    def __init__(self, **params):
        super().__init__(**params)
        
        # S3 link input
        self.s3_load_button = pn.widgets.Button(
            name="Load S3 file", button_type="primary", width=200
        )
        self.s3_load_button.on_click(self._load_s3_file)
        self.s3_link = pn.widgets.TextInput(
            name="S3 Link",
            placeholder="Enter S3 link to a file",
            width=300,
        )
        self.s3_link.param.watch(
            self._load_s3_file, ["enter_pressed"], onlychanged=False
        )

    @catch_and_notify(prefix="<b>Load S3 file: </b>")
    def _load_s3_file(self, event):
        s3_path = self.s3_link.value
        if s3_path:
            logger.info(f"Selected file: {s3_path}")
            self._after_path_select(s3_path)
        else:
            logger.info("No file selected.")
    
    @catch_and_notify(prefix="<b>Open file: </b>")
    def _after_path_select(self, file_path: str):
        if self.process_path_fn is not None:
            self.process_path_fn(file_path)
    
    def set_update_function(self, func):
        """
        Set the function to be called when a file is selected.
        This function should accept a single argument, which is the path to the selected file.
        """
        self.process_path_fn = func

    def __panel__(self):
        return pn.layout.FlexBox(self.s3_link, self.s3_load_button)