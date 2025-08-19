__version__ = "0.1.0"

import sys

from .bls_file_input import BlsFileInput
from .bls_data_visualizer import BlsDataVisualizer
from .bls_spectrum import BlsSpectrumVisualizer
from .s3file_selector import S3FileSelector
from .bls_metadata import BlsMetadata

# Keep treatment widget out of the wasm package
if "pyodide" in sys.modules:
    #from .browser_file_selectors import JSFileInput
    pass #JSFileInput needs to be in the main python file, not in the widgets package
else:
    from .local_file_selectors import TinkerFileSelector
    from .bls_do_treatment import BlsDoTreatment