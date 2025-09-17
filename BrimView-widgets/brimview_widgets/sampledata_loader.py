import panel as pn

from .utils import catch_and_notify, is_running_from_docker

class SampledataLoader(pn.viewable.Viewer):

    _sampledata = {
        "Drosophila - LSBM": "https://storage.googleapis.com/brim-example-files/drosophila_LSBM.brim.zarr",
        "Zebrafish eye - confocal": "https://storage.googleapis.com/brim-example-files/zebrafish_eye_confocal.brim.zarr",
        "Zebrafish ECM - SBS": "https://storage.googleapis.com/brim-example-files/zebrafish_ECM_SBS.brim.zarr",
        "Oil beads - FTBM": "https://storage.googleapis.com/brim-example-files/oil_beads_FTBM.brim.zarr"
    }
    if is_running_from_docker():
        print("Loading sample data from EMBL S3 bucket")
        _sampledata = {
        "Drosophila - LSBM": "https://s3.embl.de/brim-example-files/drosophila_LSBM.brim.zarr",
        "Zebrafish eye - confocal": "https://s3.embl.de/brim-example-files/zebrafish_eye_confocal.brim.zarr",
        "Zebrafish ECM - SBS": "https://s3.embl.de/brim-example-files/zebrafish_ECM_SBS.brim.zarr",
        "Oil beads - FTBM": "https://s3.embl.de/brim-example-files/oil_beads_FTBM.brim.zarr"
        }

    def __init__(self, **params):
        super().__init__(**params)
        
        # S3 link input
        self.sampledata_load_button = pn.widgets.Button(
            name="Load sample", button_type="primary", width=200
        )
        self.sampledata_load_button.on_click(self._load_s3_file)
        self.s3_link = pn.widgets.Select(
            name='Dataset', 
            options=list(self._sampledata.keys()),
            width=300)

    @catch_and_notify(prefix="<b>Load S3 file: </b>")
    def _load_s3_file(self, event):
        s3_path = self._sampledata[self.s3_link.value]
        if s3_path:
            print(f"Selected file: {s3_path}")
            self._after_path_select(s3_path)
        else:
            print("No file selected.")
    
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
        return pn.Card(
            pn.layout.FlexBox(self.s3_link, self.sampledata_load_button),
            title="Sample data",
            collapsed=True,
            collapsible=True,
            sizing_mode="stretch_width",
            margin=5
        )