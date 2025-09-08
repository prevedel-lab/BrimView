import { ZarrFile, init_file } from './zarr_file.js';

// Loads the Zarr and create a bls_file in the globals of pyodide
function loadZarrFile(file) {
    const {zarr_file_js , filename}  = init_file(file)

    const locals = pyodide.toPy({ zarr_file_js: zarr_file_js, zarr_filename: filename });
    pyodide.runPython(`
        import brimfile as bls

        from brimfile.file_abstraction import _zarrFile
        from brimview_widgets import CustomJSFileInput
        
        zf = _zarrFile(zarr_file_js, filename=zarr_filename)
        bls_file = bls.File(zf)
        CustomJSFileInput().set_global_bls(bls_file)
    `, { locals });
    return true;
}
//make sure loadZarrFile is in the global scope
self.loadZarrFile = loadZarrFile;