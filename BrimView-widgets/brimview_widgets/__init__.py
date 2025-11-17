__version__ = "0.3.2"

import sys

from .bls_file_input import BlsFileInput
from .bls_data_visualizer import BlsDataVisualizer
from .bls_spectrum import BlsSpectrumVisualizer
from .s3file_selector import S3FileSelector
from . sampledata_loader import SampledataLoader
from .bls_metadata import BlsMetadata
from .debug_report_widget import DebugReport
from .environment import running_from_pyodide

# Keep treatment widget out of the wasm package
if running_from_pyodide:
    from .browser_file_selectors import CustomJSFileInput
    pass #JSFileInput needs to be in the main python file, not in the widgets package
else:
    from .browser_file_selectors import CustomJSFileInput
    from .local_file_selectors import TinkerFileSelector
    from .bls_do_treatment import BlsDoTreatment